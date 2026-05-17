from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.middleware.hospital_context import get_current_hospital_id
from app.utils.exceptions import ValidationException
from app.modules.dispatch import schemas, service

router = APIRouter()


def _get_hospital_id() -> str:
    hid = get_current_hospital_id()
    if not hid:
        raise ValidationException(detail="Hospital context required")
    return hid


def _get_db(hospital_id: str = Depends(_get_hospital_id)):
    return next(get_hospital_db(hospital_id))


@router.get("/metrics/current", response_model=schemas.ResourceMetricResponse)
def get_current_metrics():
    return service.get_resource_metrics()


@router.get("/queues")
def get_queue_status():
    return service.get_queue_status()


@router.get("/config")
def get_config(db: Session = Depends(_get_db)):
    return service.get_config(db)


@router.put("/config")
def update_config(data: schemas.DispatchConfigUpdate, db: Session = Depends(_get_db)):
    updates = data.model_dump(exclude_none=True)
    return service.update_config(db, updates)
