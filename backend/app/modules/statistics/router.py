from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date

from app.core.database import get_hospital_db
from app.middleware.hospital_context import get_current_hospital_id
from app.utils.exceptions import ValidationException
from app.modules.statistics import schemas, service

router = APIRouter()


def _get_hospital_id() -> str:
    hid = get_current_hospital_id()
    if not hid:
        raise ValidationException(detail="Hospital context required")
    return hid


def _get_db(hospital_id: str = Depends(_get_hospital_id)):
    return next(get_hospital_db(hospital_id))


@router.get("/dashboard")
def dashboard(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(_get_db),
):
    return service.dashboard_overview(db, str(start_date), str(end_date))


@router.get("/health-profile")
def health_profile(
    start_date: date = Query(...),
    end_date: date = Query(...),
    unit_name: Optional[str] = Query(None),
    db: Session = Depends(_get_db),
):
    return service.health_profile(db, str(start_date), str(end_date), unit_name)


@router.get("/cross-compare")
def cross_compare(
    start_date: date = Query(...),
    end_date: date = Query(...),
    x_dimension: str = Query("unit"),
    unit_name: Optional[str] = Query(None),
    db: Session = Depends(_get_db),
):
    return service.cross_compare(db, str(start_date), str(end_date), x_dimension, unit_name)


@router.get("/trend")
def trend(
    indicator: str = Query(...),
    years: int = Query(5, ge=1, le=10),
    db: Session = Depends(_get_db),
):
    return service.trend_analysis(db, indicator, years)


@router.post("/export")
def export_report(req: schemas.ExportRequest, db: Session = Depends(_get_db)):
    return {"status": "queued", "message": "Export task submitted"}
