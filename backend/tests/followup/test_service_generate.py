"""try_generate_followup:触发 / 跳过 / 幂等 / 快照 / 吞错。"""
from datetime import date
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from app.models.base import Base
from app.modules.report.models import ReportInfo, ReportIndicator
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from app.modules.followup.models import (  # noqa: F401
    Followup, FollowupQuestion, UserNotification,
    FollowupTemplate, FollowupTemplateQuestion,
)
from app.modules.followup import service


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _tdb_gen(session):
    yield session


def _seed_template(session, questions=None):
    questions = questions or [
        {"question_type": "single", "question_text": "近期是否头晕？",
         "options": ["经常", "偶尔", "无"], "is_required": 1, "sort_order": 1, "is_active": 1},
        {"question_type": "text", "question_text": "补充说明",
         "options": None, "is_required": 0, "sort_order": 2, "is_active": 1},
    ]
    tpl = FollowupTemplate(name="通用检后随访", is_active=1)
    session.add(tpl)
    session.flush()
    for q in questions:
        session.add(FollowupTemplateQuestion(template_id=tpl.id, **q))
    session.commit()
    return tpl


def _seed_red_report(session, level="red"):
    r = ReportInfo(user_id="123456", name="张三", report_date=date(2026, 8, 30))
    session.add(r)
    session.flush()
    session.add(ReportIndicator(report_id=r.id, item_name="甘油三酯",
                                result_value="2.8", unit="mmol/L",
                                ref_range_low="0.4", ref_range_high="1.7"))
    session.add(ReportIndicator(report_id=r.id, item_name="谷丙转氨酶",
                                result_value="60", unit="U/L"))
    session.flush()
    interp = ReportInterpretation(report_id=r.id, overall_level=level, status="completed")
    session.add(interp)
    session.flush()
    session.add(IndicatorJudgment(interpretation_id=interp.id, indicator_id=1,
                                  item_name="甘油三酯", result_value="2.8",
                                  color_level="red", deviation="high"))
    session.add(IndicatorJudgment(interpretation_id=interp.id, indicator_id=2,
                                  item_name="谷丙转氨酶", result_value="60",
                                  color_level="yellow", deviation="high"))
    session.commit()
    return r.id


def _patch_tdb(session):
    return patch("app.modules.followup.service.get_template_db",
                 lambda: _tdb_gen(session))


def test_red_report_creates_followup_and_notification(db):
    rid = _seed_red_report(db, "red")
    _seed_template(db)
    with _patch_tdb(db):
        ok = service.try_generate_followup(db, rid)
    assert ok is True
    fup = db.query(Followup).filter_by(report_id=rid).first()
    assert fup.status == "pending"
    assert fup.user_id == "123456"
    assert fup.overall_level == "red"
    assert fup.template_name == "通用检后随访"
    assert len(fup.recheck_indicators_json) == 2
    by_name = {it["item_name"]: it for it in fup.recheck_indicators_json}
    assert set(by_name) == {"甘油三酯", "谷丙转氨酶"}
    assert by_name["甘油三酯"]["color_level"] == "red"
    assert by_name["甘油三酯"]["ref_range"] == "0.4-1.7"
    qs = (db.query(FollowupQuestion).filter_by(followup_id=fup.id)
          .order_by(FollowupQuestion.sort_order).all())
    assert [q.question_text for q in qs] == ["近期是否头晕？", "补充说明"]
    notif = db.query(UserNotification).filter_by(user_id="123456").first()
    assert notif.category == "recheck_reminder"
    assert notif.ref_followup_id == fup.id
    assert notif.content["overall_level"] == "red"
    assert "2 项异常" in notif.title


def test_green_report_skips(db):
    rid = _seed_red_report(db, "green")
    _seed_template(db)
    with _patch_tdb(db):
        assert service.try_generate_followup(db, rid) is False
    assert db.query(Followup).filter_by(report_id=rid).count() == 0
    assert db.query(UserNotification).count() == 0


def test_duplicate_call_idempotent(db):
    rid = _seed_red_report(db, "yellow")
    _seed_template(db)
    with _patch_tdb(db):
        assert service.try_generate_followup(db, rid) is True
        assert service.try_generate_followup(db, rid) is False
    assert db.query(Followup).filter_by(report_id=rid).count() == 1
    assert db.query(UserNotification).count() == 1


def test_no_active_template_skips(db):
    rid = _seed_red_report(db)
    with _patch_tdb(db):  # 无模板行
        assert service.try_generate_followup(db, rid) is False
    assert db.query(Followup).count() == 0
    assert db.query(UserNotification).count() == 0


def test_inactive_question_not_snapshotted(db):
    rid = _seed_red_report(db)
    _seed_template(db, questions=[
        {"question_type": "text", "question_text": "启用题",
         "options": None, "is_required": 1, "sort_order": 1, "is_active": 1},
        {"question_type": "text", "question_text": "停用题",
         "options": None, "is_required": 1, "sort_order": 2, "is_active": 0},
    ])
    with _patch_tdb(db):
        service.try_generate_followup(db, rid)
    fup = db.query(Followup).filter_by(report_id=rid).first()
    qs = db.query(FollowupQuestion).filter_by(followup_id=fup.id).all()
    assert [q.question_text for q in qs] == ["启用题"]


def test_snapshot_stable_after_template_change(db):
    rid = _seed_red_report(db)
    _seed_template(db)
    with _patch_tdb(db):
        service.try_generate_followup(db, rid)
    fup = db.query(Followup).filter_by(report_id=rid).first()
    qs = db.query(FollowupQuestion).filter_by(followup_id=fup.id).all()
    assert qs[0].question_text == "近期是否头晕？"
    # 改动平台模板 → 实例快照不变
    db.query(FollowupTemplateQuestion).update({FollowupTemplateQuestion.question_text: "改后题干"})
    db.commit()
    assert db.query(FollowupQuestion).filter_by(followup_id=fup.id).first().question_text == "近期是否头晕？"


def test_failure_swallowed_and_rolled_back(db):
    rid = _seed_red_report(db)
    _seed_template(db)
    with _patch_tdb(db):
        with patch.object(service, "UserNotification", side_effect=RuntimeError("boom")):
            assert service.try_generate_followup(db, rid) is False
    assert db.query(Followup).count() == 0
    assert db.query(UserNotification).count() == 0
