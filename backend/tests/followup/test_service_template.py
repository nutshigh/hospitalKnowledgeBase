"""平台模板读写:空题拦截 / 新建激活 / 全量替换。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.followup.models import (  # noqa: F401
    FollowupTemplate, FollowupTemplateQuestion,
    Followup, FollowupQuestion, UserNotification,
)
from app.modules.followup import service
from app.utils.exceptions import ValidationException


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def test_get_empty_template(db):
    data = service.get_active_template(db)
    assert data["id"] is None
    assert data["questions"] == []


def test_save_rejects_empty_questions(db):
    with pytest.raises(ValidationException):
        service.save_active_template(db, "通用", None, [])


def test_save_rejects_bad_question_type(db):
    with pytest.raises(ValidationException):
        service.save_active_template(db, "通用", None,
                                     [{"question_type": "radio",
                                       "question_text": "x", "options": ["a"]}])


def test_save_creates_then_replaces(db):
    qs = [
        {"question_type": "single", "question_text": "近期是否头晕？",
         "options": ["经常", "偶尔", "无"], "is_required": True, "sort_order": 1},
        {"question_type": "text", "question_text": "补充说明",
         "is_required": False, "sort_order": 2},
    ]
    first = service.save_active_template(db, "通用检后随访", "desc", qs, updater_id=7)
    assert first["id"] is not None
    assert len(first["questions"]) == 2

    second = service.save_active_template(db, "通用检后随访", "desc2",
                                          [{"question_type": "text",
                                            "question_text": "只有一题",
                                            "is_required": True}])
    assert second["id"] == first["id"]
    assert len(second["questions"]) == 1
    assert second["questions"][0]["question_text"] == "只有一题"
    # 全量替换:旧题目行已清空
    assert db.query(FollowupTemplateQuestion).count() == 1
