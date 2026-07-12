from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.user_profile import service

router = APIRouter()


def _get_db(current_user: CurrentUser = Depends(get_current_user)):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@router.get("/overview")
def overview(
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return service.get_overview(db, current_user.user_id)


@router.get("/compare")
def compare(
    report_id: int = Query(...),
    baseline_id: Optional[int] = Query(None),
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    result = service.get_comparison(db, current_user.user_id, report_id, baseline_id)
    if not result:
        raise NotFoundException(detail="Report not found")
    return result


@router.get("/ai-summary")
def ai_summary(
    report_id: int = Query(...),
    baseline_id: int = Query(...),
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    summary, cached = service.get_ai_summary(db, current_user.user_id, report_id, baseline_id)
    return {"ai_summary": summary, "cached": cached}