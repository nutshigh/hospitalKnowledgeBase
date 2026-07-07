from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session

from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment, TriageRule
from app.modules.interpretation.rules_engine import rules_engine
from app.modules.interpretation.schemas import TriageRuleCreate, TriageRuleUpdate
from app.modules.report.models import ReportInfo, ReportIndicator
from app.core.rabbitmq import rabbitmq, TaskMessage


# ---- Triage Rules CRUD ----

def list_rules(db: Session) -> List[TriageRule]:
    return db.query(TriageRule).order_by(TriageRule.priority).all()


def get_rule(db: Session, rule_id: int) -> Optional[TriageRule]:
    return db.query(TriageRule).filter(TriageRule.id == rule_id).first()


def create_rule(db: Session, data: TriageRuleCreate) -> TriageRule:
    rule = TriageRule(
        rule_name=data.rule_name, rule_type=data.rule_type,
        indicator_code=data.indicator_code, conditions=data.conditions,
        color_level=data.color_level, priority=data.priority,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def update_rule(db: Session, rule_id: int, data: TriageRuleUpdate) -> Optional[TriageRule]:
    rule = get_rule(db, rule_id)
    if not rule:
        return None
    for field in ("rule_name", "rule_type", "indicator_code", "conditions", "color_level", "priority", "is_active"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(rule, field, val)
    db.commit()
    db.refresh(rule)
    return rule


def delete_rule(db: Session, rule_id: int) -> bool:
    rule = get_rule(db, rule_id)
    if not rule:
        return False
    db.delete(rule)
    db.commit()
    return True


# ---- Interpretation Pipeline ----

def process_interpretation(db: Session, report_id: int, hospital_id: str):
    """触发 interpretation Agent 处理（薄包装，实际逻辑在 ai/agents/interp_graph.py）"""
    from app.ai.agents import run_interpretation_agent
    run_interpretation_agent(hospital_id, db, report_id)


def get_interpretation(db: Session, report_id: int) -> Optional[ReportInterpretation]:
    return db.query(ReportInterpretation).filter(ReportInterpretation.report_id == report_id).first()


def get_judgments(db: Session, interpretation_id: int) -> List[IndicatorJudgment]:
    return db.query(IndicatorJudgment).filter(IndicatorJudgment.interpretation_id == interpretation_id).all()


def get_judgments_with_indicator_detail(db: Session, interpretation_id: int) -> list[dict]:
    """join indicator_judgment 与 report_indicator，返回前端展示所需字段

    含 deviation/color_level（来自 judgment）+ unit/ref_range_low/ref_range_high（来自 indicator）
    """
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT j.indicator_id, j.item_name, j.result_value, j.deviation, j.color_level, "
        "i.unit, i.ref_range_low, i.ref_range_high "
        "FROM indicator_judgment j "
        "LEFT JOIN report_indicator i ON i.id = j.indicator_id "
        "WHERE j.interpretation_id = :iid ORDER BY j.id"
    ), {"iid": interpretation_id}).fetchall()
    return [
        {"indicator_id": r[0], "item_name": r[1], "result_value": r[2],
         "deviation": r[3], "color_level": r[4], "unit": r[5],
         "ref_range_low": r[6], "ref_range_high": r[7]}
        for r in rows
    ]


def get_high_risk_list(db: Session, hospital_id: str) -> List[dict]:
    rows = (
        db.query(ReportInterpretation, ReportInfo)
        .join(ReportInfo, ReportInterpretation.report_id == ReportInfo.id)
        .filter(ReportInterpretation.overall_level == "red")
        .order_by(ReportInterpretation.red_count.desc())
        .all()
    )
    return [
        {"interpretation_id": i.id, "report_id": i.report_id, "user_id": r.user_id,
         "name": r.name, "unit_name": r.unit_name, "red_count": i.red_count,
         "created_at": i.created_at}
        for i, r in rows
    ]
