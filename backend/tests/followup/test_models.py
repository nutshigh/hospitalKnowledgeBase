"""模型骨架:5 张表能创建,唯一约束在库层生效。"""
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.followup.models import (  # noqa: F401 — 注册到 Base.metadata
    FollowupTemplate, FollowupTemplateQuestion,
    Followup, FollowupQuestion, UserNotification,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def test_all_tables_created(db):
    names = set(inspect(db.get_bind()).get_table_names())
    assert {"followup_template", "followup_template_question",
            "followup", "followup_question", "user_notification"} <= names


def test_followup_report_unique(db):
    from sqlalchemy.exc import IntegrityError
    db.add(Followup(report_id=1, user_id="123456", name="张三", overall_level="red"))
    db.add(Followup(report_id=1, user_id="123456", name="张三", overall_level="red"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
