from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session

from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment, TriageRule
from app.modules.interpretation.rules_engine import rules_engine
from app.modules.interpretation.schemas import TriageRuleCreate, TriageRuleUpdate
from app.modules.report.models import ReportInfo, ReportIndicator
from app.core.llm_client import llm_client
from app.core.rabbitmq import rabbitmq, TaskMessage
import httpx


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
    report = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not report:
        return

    # Skip if already completed
    existing = db.query(ReportInterpretation).filter(
        ReportInterpretation.report_id == report_id,
        ReportInterpretation.status == "completed",
    ).first()
    if existing:
        return

    # Delete any incomplete previous interpretations for this report
    db.query(ReportInterpretation).filter(
        ReportInterpretation.report_id == report_id,
    ).delete()
    db.commit()

    interp = ReportInterpretation(report_id=report_id, status="processing")
    db.add(interp)
    db.commit()
    db.refresh(interp)

    try:
        indicators = db.query(ReportIndicator).filter(ReportIndicator.report_id == report_id).all()

        rules = list_rules(db)
        rules_engine.load_rules(hospital_id, [{
            "id": r.id, "rule_name": r.rule_name, "rule_type": r.rule_type,
            "indicator_code": r.indicator_code, "conditions": r.conditions,
            "color_level": r.color_level, "priority": r.priority, "is_active": r.is_active,
        } for r in rules])

        red_count = yellow_count = green_count = 0

        for ind in indicators:
            ind_dict = {
                "item_name": ind.item_name, "item_name_standard": ind.item_name_standard,
                "result_value": ind.result_value, "unit": ind.unit,
                "ref_range_low": ind.ref_range_low, "ref_range_high": ind.ref_range_high,
            }
            result = rules_engine.evaluate(hospital_id, ind_dict)

            deviation = result.deviation
            if deviation == "normal":
                try:
                    val = float(ind.result_value or 0)
                    ref_high = float(ind.ref_range_high or 0)
                    ref_low = float(ind.ref_range_low or 0)
                    if ref_high and val > ref_high:
                        deviation = "high"
                    elif ref_low and val < ref_low:
                        deviation = "low"
                except (ValueError, TypeError):
                    pass

            explanation = ""
            suggestion = ""
            if result.color_level != "green":
                try:
                    knowledge_context = _fetch_knowledge(hospital_id, ind.item_name, ind.result_value or "")
                    response = llm_client.interpret_indicator(
                        {**ind_dict, "deviation": deviation, "color_level": result.color_level},
                        knowledge_context,
                    )
                    explanation = response
                    suggestion = response
                except Exception:
                    pass

            db.add(IndicatorJudgment(
                interpretation_id=interp.id, indicator_id=ind.id,
                item_name=ind.item_name, result_value=ind.result_value,
                deviation=deviation, color_level=result.color_level,
                matched_rule_id=result.matched_rule_id,
                explanation=explanation, suggestion=suggestion,
            ))

            if result.color_level == "red":
                red_count += 1
            elif result.color_level == "yellow":
                yellow_count += 1
            else:
                green_count += 1

        db.commit()

        overall = "green"
        if red_count > 0:
            overall = "red"
        elif yellow_count > 0:
            overall = "yellow"

        interp.red_count = red_count
        interp.yellow_count = yellow_count
        interp.green_count = green_count
        interp.overall_level = overall
        interp.status = "completed"
        interp.completed_at = datetime.utcnow()
        db.commit()

        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=0,
            payload={"event": "interpretation_done", "report_id": report_id, "hospital_id": hospital_id},
        ))

    except Exception as e:
        interp.retry_count += 1
        interp.status = "failed" if interp.retry_count >= 3 else "pending"
        db.commit()


def _fetch_knowledge(hospital_id: str, item_name: str, result_value: str) -> str:
    try:
        query = f"{item_name} {result_value}"
        response = httpx.post(
            "http://localhost:8000/api/v1/knowledge/internal/search",
            json={"hospital_id": hospital_id, "query": query, "top_k": 3},
            timeout=10.0,
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                return "\n".join(f"[{r['title']}] {r['content']}" for r in results)
    except Exception:
        pass
    return ""


def get_interpretation(db: Session, report_id: int) -> Optional[ReportInterpretation]:
    return db.query(ReportInterpretation).filter(ReportInterpretation.report_id == report_id).first()


def get_judgments(db: Session, interpretation_id: int) -> List[IndicatorJudgment]:
    return db.query(IndicatorJudgment).filter(IndicatorJudgment.interpretation_id == interpretation_id).all()


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
