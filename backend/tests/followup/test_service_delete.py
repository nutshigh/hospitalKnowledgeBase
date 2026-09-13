"""删除报告联动:清随访/答卷/通知,不动他人数据。"""
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


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _seed(db, report_id=1, uid="123456"):
    db.add(ReportInfo(id=report_id, user_id=uid, name="张三"))
    f = Followup(report_id=report_id, user_id=uid, name="张三", overall_level="red")
    db.add(f); db.flush()
    db.add(FollowupQuestion(followup_id=f.id, question_type="text",
                            question_text="q", is_required=1, sort_order=1))
    db.add(UserNotification(user_id=uid, name="张三", category="recheck_reminder",
                            title="t", content={"a": 1},
                            ref_report_id=report_id, ref_followup_id=f.id))
    db.commit()
    return f


def test_delete_report_followup_cleans(db):
    _seed(db, report_id=1)
    _seed(db, report_id=2)  # 无关报告,须保留
    service.delete_report_followup(db, 1)
    assert db.query(Followup).filter_by(report_id=1).count() == 0
    assert db.query(FollowupQuestion).filter_by(followup_id=1).count() == 0
    assert db.query(UserNotification).filter(UserNotification.ref_report_id == 1).count() == 0
    assert db.query(Followup).filter_by(report_id=2).count() == 1
    assert db.query(UserNotification).filter(UserNotification.ref_report_id == 2).count() == 1
