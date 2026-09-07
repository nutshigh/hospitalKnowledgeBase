"""用户侧查询/提交 + 通知已读 + 医生 by-report。"""
from datetime import date, datetime
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportInfo
from app.modules.interpretation.models import ReportInterpretation
from app.modules.followup.models import (  # noqa: F401
    Followup, FollowupQuestion, UserNotification,
    FollowupTemplate, FollowupTemplateQuestion,
)
from app.modules.followup import service
from app.utils.exceptions import NotFoundException, ValidationException


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _mk_followup(db, fid=1, uid="123456", nm="张三", status="pending"):
    db.add(ReportInfo(id=fid, user_id=uid, name=nm))
    db.add(ReportInterpretation(report_id=fid, status="completed", overall_level="yellow"))
    f = Followup(id=fid, report_id=fid, user_id=uid, name=nm,
                 overall_level="yellow", status=status,
                 recheck_indicators_json=[{"item_name": "血压", "color_level": "yellow"}],
                 template_name="通用检后随访")
    db.add(f)
    db.flush()
    db.add(FollowupQuestion(followup_id=fid, question_type="single",
                            question_text="近期是否头晕？", options=["是", "否"],
                            is_required=1, sort_order=1))
    db.add(FollowupQuestion(followup_id=fid, question_type="multiple",
                            question_text="有哪些症状？", options=["头痛", "心悸"],
                            is_required=0, sort_order=2))
    db.add(FollowupQuestion(followup_id=fid, question_type="text",
                            question_text="补充", options=None,
                            is_required=0, sort_order=3))
    db.commit()
    return f


def test_list_only_own(db):
    _mk_followup(db, fid=1, uid="123456", nm="张三")
    _mk_followup(db, fid=2, uid="999999", nm="李四")
    r = service.list_my_followups(db, "123456", "张三", 1, 20)
    assert r["total"] == 1
    assert r["items"][0]["report_id"] == 1
    assert r["has_pending"] is True


def test_get_detail_ownership(db):
    _mk_followup(db, fid=1)
    assert service.get_followup_detail(db, "123456", "张三", 1) is not None
    assert service.get_followup_detail(db, "999999", "李四", 1) is None


def test_submit_requires_required(db):
    _mk_followup(db, fid=1)
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1, [{"question_id": 2, "answer": ["头痛"]}])


def test_submit_rejects_unknown_question(db):
    _mk_followup(db, fid=1)
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1, [{"question_id": 99, "answer": "x"}])


def test_submit_rejects_invalid_option(db):
    _mk_followup(db, fid=1)
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1,
                                [{"question_id": 1, "answer": "不存在"}])
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1,
                                [{"question_id": 2, "answer": ["不存在"]}])


def test_submit_duplicate_question_id(db):
    _mk_followup(db, fid=1)
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1,
                                [{"question_id": 1, "answer": "是"},
                                 {"question_id": 1, "answer": "否"}])


def test_submit_success_marks_completed(db):
    _mk_followup(db, fid=1)
    service.submit_followup(db, "123456", "张三", 1, [
        {"question_id": 1, "answer": "是"},
        {"question_id": 2, "answer": ["头痛", "心悸"]},
        {"question_id": 3, "answer": "暂无"},
    ])
    f = db.query(Followup).filter_by(id=1).first()
    assert f.status == "completed"
    assert f.submitted_at is not None
    qs = {q.id: q for q in db.query(FollowupQuestion).filter_by(followup_id=1).all()}
    assert qs[1].answer == "是"
    assert json.loads(qs[2].answer) == ["头痛", "心悸"]
    with pytest.raises(ValidationException):
        service.submit_followup(db, "123456", "张三", 1, [])


def test_notifications_read_flow(db):
    _mk_followup(db, fid=1)
    n1 = UserNotification(user_id="123456", name="张三", category="recheck_reminder",
                          title="t1", content={"a": 1}, ref_report_id=1, ref_followup_id=1, is_read=0)
    n2 = UserNotification(user_id="123456", name="张三", category="recheck_reminder",
                          title="t2", content={"a": 2}, ref_report_id=1, ref_followup_id=1, is_read=0)
    db.add_all([n1, n2]); db.commit()
    assert service.count_unread(db, "123456", "张三") == 2
    assert service.count_unread(db, "999999", "李四") == 0
    r = service.list_my_notifications(db, "123456", "张三", 1, 20)
    assert r["total"] == 2
    assert service.mark_notification_read(db, "123456", "张三", n1.id) is True
    assert service.count_unread(db, "123456", "张三") == 1
    assert service.mark_notification_read(db, "999999", "李四", n1.id) is False
    assert service.mark_all_notifications_read(db, "123456", "张三") == 1


def test_doctor_by_report(db):
    _mk_followup(db, fid=1)
    d = service.get_followup_by_report(db, 1)
    assert d["status"] == "pending"
    assert len(d["questions"]) == 3
    assert service.get_followup_by_report(db, 999) is None
