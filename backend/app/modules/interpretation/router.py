from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.interpretation import schemas, service
from app.modules.interpretation.schemas import (
    IndicatorJudgmentSchema, CitationSchema, parse_summary_text,
)

router = APIRouter()


def _get_db(
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


# ---- Static paths MUST come before dynamic {report_id} ----

@router.get("/high-risk/list", response_model=schemas.HighRiskResponse)
def get_high_risk_list(
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    items = service.get_high_risk_list(db, current_user.hospital_id)
    return {"items": items, "total": len(items)}


@router.get("/rules/all", response_model=list[schemas.TriageRuleResponse])
def list_rules(db: Session = Depends(_get_db)):
    return service.list_rules(db)


@router.post("/rules", response_model=schemas.TriageRuleResponse)
def create_rule(data: schemas.TriageRuleCreate, db: Session = Depends(_get_db)):
    return service.create_rule(db, data)


@router.put("/rules/{rule_id}", response_model=schemas.TriageRuleResponse)
def update_rule(rule_id: int, data: schemas.TriageRuleUpdate, db: Session = Depends(_get_db)):
    rule = service.update_rule(db, rule_id, data)
    if not rule:
        raise NotFoundException(detail="Rule not found")
    return rule


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(_get_db)):
    if not service.delete_rule(db, rule_id):
        raise NotFoundException(detail="Rule not found")
    return {"status": "deleted"}


# ---- Dynamic {report_id} path LAST ----

@router.get("/{report_id}", response_model=schemas.InterpretationResponse)
def get_interpretation(report_id: int, db: Session = Depends(_get_db)):
    interp = service.get_interpretation(db, report_id)
    if not interp:
        raise NotFoundException(detail="Interpretation not found")
    rows = service.get_judgments_with_indicator_detail(db, interp.id)
    summaries = parse_summary_text(interp.summary_text)
    references = [CitationSchema(**r).model_dump() for r in (interp.summary_refs or [])]
    indicators = [IndicatorJudgmentSchema(**r) for r in rows]
    return {
        "id": interp.id, "report_id": interp.report_id,
        "overall_level": interp.overall_level,
        "red_count": interp.red_count, "yellow_count": interp.yellow_count,
        "green_count": interp.green_count,
        "status": interp.status,
        "summaries": summaries,
        "references": references,
        "quality_note": interp.quality_note,
        "indicators": indicators,
        "created_at": interp.created_at, "completed_at": interp.completed_at,
    }


@router.get("/{report_id}/indicators")
def get_judgments(report_id: int, db: Session = Depends(_get_db)):
    interp = service.get_interpretation(db, report_id)
    if not interp:
        raise NotFoundException(detail="Interpretation not found")
    return service.get_judgments(db, interp.id)
