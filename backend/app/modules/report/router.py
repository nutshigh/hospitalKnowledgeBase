import os
import uuid
from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.report import schemas, service
from app.config import settings

router = APIRouter()

ALLOWED_TYPES = {
    "pdf": "pdf", "docx": "docx", "doc": "docx",
    "jpg": "image", "jpeg": "image", "png": "image",
}
MAX_FILE_SIZE = 20 * 1024 * 1024


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


@router.post("/upload")
def upload_report(
    file: UploadFile = File(...),
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
    file_type = ALLOWED_TYPES.get(ext)
    if not file_type:
        raise ValidationException(detail=f"Unsupported format. Allowed: {list(ALLOWED_TYPES.keys())}")

    storage_dir = os.path.join(settings.FILE_STORAGE_ROOT, current_user.hospital_id, "reports", str(current_user.user_id))
    os.makedirs(storage_dir, exist_ok=True)
    file_id = uuid.uuid4().hex
    file_path = os.path.join(storage_dir, f"{file_id}.{ext}")
    content = file.file.read()
    if len(content) > MAX_FILE_SIZE:
        raise ValidationException(detail="File too large (max 20MB)")
    with open(file_path, "wb") as f:
        f.write(content)

    task = service.create_task(
        db=db, hospital_id=current_user.hospital_id, user_id=current_user.user_id,
        file_path=file_path, filename=file.filename, file_type=file_type,
        file_size=os.path.getsize(file_path),
    )
    return schemas.TaskStatusResponse(
        task_id=task.id, status=task.status, error_message=None,
        created_at=task.created_at, completed_at=None,
    )


@router.get("/tasks/{task_id}", response_model=schemas.TaskStatusResponse)
def get_task_status(task_id: int, db: Session = Depends(_get_db)):
    task = service.get_task_status(db, task_id)
    if not task:
        raise NotFoundException(detail="Task not found")
    return schemas.TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        error_message=task.error_message,
        created_at=task.created_at,
        completed_at=task.completed_at,
    )


@router.get("")
def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = None if current_user.role != "user" else current_user.user_id
    items, total = service.list_reports(db, current_user.hospital_id, user_id, page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{report_id}", response_model=schemas.ReportDetailResponse)
def get_report_detail(report_id: int, db: Session = Depends(_get_db)):
    report = service.get_report_detail(db, report_id)
    if not report:
        raise NotFoundException(detail="Report not found")
    indicators = service.get_report_indicators(db, report_id)
    return {
        "id": report.id, "task_id": report.task_id,
        "name": report.name, "gender": report.gender, "age": report.age,
        "report_date": report.report_date, "check_type": report.check_type,
        "unit_name": report.unit_name,
        "indicators": [
            {"item_name": i.item_name, "item_name_standard": i.item_name_standard,
             "item_code": i.item_code, "result_value": i.result_value,
             "unit": i.unit, "ref_range_low": i.ref_range_low,
             "ref_range_high": i.ref_range_high, "category": i.category}
            for i in indicators
        ],
        "created_at": report.created_at,
    }


@router.delete("/{report_id}")
def delete_report(report_id: int, db: Session = Depends(_get_db)):
    report = service.get_report_detail(db, report_id)
    if not report:
        raise NotFoundException(detail="Report not found")
    from sqlalchemy import text
    db.execute(text("DELETE FROM indicator_judgment WHERE interpretation_id IN (SELECT id FROM report_interpretation WHERE report_id = :rid)"), {"rid": report_id})
    db.execute(text("DELETE FROM report_interpretation WHERE report_id = :rid"), {"rid": report_id})
    db.execute(text("DELETE FROM report_indicator WHERE report_id = :rid"), {"rid": report_id})
    db.execute(text("DELETE FROM chat_message WHERE session_id IN (SELECT id FROM chat_session WHERE report_id = :rid)"), {"rid": report_id})
    db.execute(text("DELETE FROM chat_session WHERE report_id = :rid"), {"rid": report_id})
    if report.task_id:
        db.execute(text("DELETE FROM report_task WHERE id = :tid"), {"tid": report.task_id})
    db.delete(report)
    db.commit()
    return {"status": "deleted"}
