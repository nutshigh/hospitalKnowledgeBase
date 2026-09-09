from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser, user_identity
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
    uid, nm = user_identity(current_user)
    if uid is None:
        return {"user_summary": None, "indicator_trends": [], "abnormal_distribution": []}
    return service.get_overview(db, uid, nm)


@router.get("/change-overview")
def change_overview(
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    uid, nm = user_identity(current_user)
    if uid is None:
        return service.empty_change_overview(0)
    return service.get_change_overview(db, uid, nm)