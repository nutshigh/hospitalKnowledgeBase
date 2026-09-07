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


# --------------------------------------------------------------------------
# 生成(interpretation worker 钩子,医院库会话;模板库会话函数内自开)
# --------------------------------------------------------------------------
def _fmt(dt):
    return dt.isoformat() if dt else None


def _load_active_template() -> "tuple[Optional[FollowupTemplate], list[FollowupTemplateQuestion]]":
    gen = get_template_db()
    db = next(gen)
    try:
        tpl = (db.query(FollowupTemplate).filter_by(is_active=1)
               .order_by(FollowupTemplate.id.desc()).first())
        if not tpl:
            return None, []
        qs = (db.query(FollowupTemplateQuestion)
              .filter(FollowupTemplateQuestion.template_id == tpl.id,
                      FollowupTemplateQuestion.is_active == 1)
              .order_by(FollowupTemplateQuestion.sort_order,
                        FollowupTemplateQuestion.id).all())
        return tpl, qs
    finally:
        gen.close()


def try_generate_followup(db: Session, report_id: int) -> bool:
    """解读 worker 钩子:红/黄报告 → 建随访问卷(pending)+ 复查提醒。

    绿/无解读/同 report_id 已存在/无激活模板 → False(不报错)。
    任意异常 → rollback + app.followup 日志,不上抛(与 comparison summary 同款容错)。
    """
    try:
        return _do_generate_followup(db, report_id)
    except Exception:
        db.rollback()
        _log.exception("followup generate failed report_id=%s", report_id)
        return False


def _do_generate_followup(db: Session, report_id: int) -> bool:
    report = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not report:
        return False
    interp = (db.query(ReportInterpretation)
              .filter(ReportInterpretation.report_id == report_id)
              .order_by(ReportInterpretation.id.desc()).first())
    if not interp or interp.status != "completed" or interp.overall_level not in ("red", "yellow"):
        return False
    if db.query(Followup).filter(Followup.report_id == report_id).first():
        return False
    tpl, questions = _load_active_template()
    if not tpl:
        _log.warning("followup skip: no active template report_id=%s", report_id)
        return False

    rows = (db.query(IndicatorJudgment, ReportIndicator)
            .join(ReportIndicator, IndicatorJudgment.indicator_id == ReportIndicator.id)
            .filter(IndicatorJudgment.interpretation_id == interp.id,
                    IndicatorJudgment.color_level.in_(["red", "yellow"]))
            .all())
    recheck = []
    for j, ind in rows:
        lo, hi = ind.ref_range_low, ind.ref_range_high
        recheck.append({
            "item_name": j.item_name, "result_value": j.result_value,
            "unit": ind.unit,
            "ref_range": f"{lo}-{hi}" if lo and hi else None,
            "color_level": j.color_level,
        })

    report_date = report.report_date.isoformat() if report.report_date else None
    fup = Followup(report_id=report_id, user_id=report.user_id, name=report.name,
                   overall_level=interp.overall_level, status="pending",
                   recheck_indicators_json=recheck, template_name=tpl.name)
    db.add(fup)
    db.flush()
    for q in questions:
        db.add(FollowupQuestion(followup_id=fup.id, question_type=q.question_type,
                                question_text=q.question_text, options=q.options,
                                is_required=q.is_required, sort_order=q.sort_order))
    n = len(recheck)
    db.add(UserNotification(
        user_id=report.user_id, name=report.name, category="recheck_reminder",
        title=f"您 {report_date or '最近'} 的体检存在 {n} 项异常,请及时关注并按需复查",
        content={"report_id": report_id, "report_date": report_date,
                 "overall_level": interp.overall_level, "followup_pending": True,
                 "recheck_indicators": recheck},
        ref_report_id=report_id, ref_followup_id=fup.id, is_read=0))
    db.commit()
    return True
