import json
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.database import get_template_db
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from app.modules.report.models import ReportInfo, ReportIndicator
from app.utils.exceptions import NotFoundException, ValidationException
from .models import (Followup, FollowupQuestion, UserNotification,
                     FollowupTemplate, FollowupTemplateQuestion)

_log = logging.getLogger("app.followup")

QUESTION_TYPES = ("single", "multiple", "text")


# --------------------------------------------------------------------------
# 平台模板(admin-portal,模板库会话)
# --------------------------------------------------------------------------
def get_active_template(db: Session) -> dict:
    tpl = (db.query(FollowupTemplate).filter_by(is_active=1)
           .order_by(FollowupTemplate.id.desc()).first())
    if not tpl:
        return {"id": None, "name": "", "description": None, "questions": []}
    qs = (db.query(FollowupTemplateQuestion)
          .filter(FollowupTemplateQuestion.template_id == tpl.id)
          .order_by(FollowupTemplateQuestion.sort_order,
                    FollowupTemplateQuestion.id).all())
    return {
        "id": tpl.id, "name": tpl.name, "description": tpl.description,
        "questions": [{
            "id": q.id, "question_type": q.question_type,
            "question_text": q.question_text, "options": q.options or [],
            "is_required": bool(q.is_required), "sort_order": q.sort_order,
            "is_active": bool(q.is_active),
        } for q in qs],
    }


def save_active_template(db: Session, name: str, description: Optional[str],
                         questions: List[dict], updater_id: Optional[int] = None) -> dict:
    if not questions:
        raise ValidationException(detail="激活模板不允许空题目")
    for i, q in enumerate(questions, start=1):
        qtype = q.get("question_type")
        qtext = str(q.get("question_text") or "").strip()
        if qtype not in QUESTION_TYPES:
            raise ValidationException(detail=f"第 {i} 题题型不合法")
        if not qtext:
            raise ValidationException(detail=f"第 {i} 题题干不能为空")
        if qtype in ("single", "multiple"):
            opts = q.get("options")
            if not isinstance(opts, list) or not all(isinstance(o, str) and o.strip() for o in opts):
                raise ValidationException(detail=f"第 {i} 题需提供非空选项列表")

    tpl = (db.query(FollowupTemplate).filter_by(is_active=1)
           .order_by(FollowupTemplate.id.desc()).first())
    if not tpl:
        tpl = FollowupTemplate(name=name or "通用检后随访", description=description,
                               is_active=1, updated_by=updater_id)
        db.add(tpl)
        db.flush()
    else:
        tpl.name = name or tpl.name
        tpl.description = description
        tpl.updated_by = updater_id

    # 全量替换:历史题目无引用(实例已快照),删除重建最稳
    db.query(FollowupTemplateQuestion).filter(
        FollowupTemplateQuestion.template_id == tpl.id).delete(synchronize_session=False)
    db.flush()
    for idx, q in enumerate(questions):
        qtype = q["question_type"]
        db.add(FollowupTemplateQuestion(
            template_id=tpl.id, question_type=qtype,
            question_text=str(q.get("question_text") or "").strip(),
            options=(q.get("options") or []) if qtype != "text" else None,
            is_required=1 if q.get("is_required", True) else 0,
            sort_order=int(q.get("sort_order") or idx),
            is_active=0 if q.get("is_active") is False else 1,
        ))
    db.commit()
    return get_active_template(db)
