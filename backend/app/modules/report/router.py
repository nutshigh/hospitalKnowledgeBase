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
    size = 0
    with open(file_path, "wb") as out:
        while True:
            buf = file.file.read(1024 * 1024)  # 1 MB block
            if not buf:
                break
            size += len(buf)
            if size > MAX_FILE_SIZE:
                out.close()
                os.remove(file_path)
                raise ValidationException(detail="File too large (max 20MB)")
            out.write(buf)

    if current_user.role == "user" and not current_user.id_card_suffix:
        raise ValidationException(
            detail="存量用户无身份证后六位,无法上传报告,请联系管理员补齐身份信息"
        )
    task = service.create_task(
        db=db, hospital_id=current_user.hospital_id,
        user_id=current_user.id_card_suffix if current_user.role == "user"
        else str(current_user.user_id),
        name=current_user.name if current_user.role == "user" else None,
        file_path=file_path, filename=file.filename, file_type=file_type,
        file_size=size,
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
    if current_user.role != "user":
        user_id = None
        name = None
    elif current_user.id_card_suffix:
        user_id = current_user.id_card_suffix
        name = current_user.name
    else:
        # 存量用户无后六位:不泄露他人报告,返回空
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    items, total = service.list_reports(db, current_user.hospital_id, user_id, name, page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{report_id}", response_model=schemas.ReportDetailResponse)
def get_report_detail(report_id: int, db: Session = Depends(_get_db)):
    report = service.get_report_detail(db, report_id)
    if not report:
        raise NotFoundException(detail="Report not found")
    indicators = service.get_report_indicators(db, report_id)
    # 展示名:与列表一致——解析出真实姓名优先;解析中(未完成)不泄露账号锚定名;
    # 已完成但未抽出姓名→回退归属锚定名。
    task_status = None
    if report.task_id:
        task = service.get_task_status(db, report.task_id)
        task_status = task.status if task else None
    if report.parsed_name:
        display_name = report.parsed_name
    elif task_status in ("queued", "parsing"):
        display_name = None
    else:
        display_name = report.name
    # 2026-09-01: 绿区展示层垃圾过滤(黄/红/结论不动)
    # 2026-09-02: "弃检/未检/放弃"类名称任何区都滤(非指标, 步新宇眼压弃检行)
    from app.modules.report.service import _clean_green_indicator, _ABANDON_ITEM_RE
    kept = []
    for i in indicators:
        if i.raw_text:
            kept.append(i)
            continue
        if _ABANDON_ITEM_RE.search(i.item_name):
            continue
        if not i.signal_flag:
            clean = _clean_green_indicator(i.item_name, i.result_value or "")
            if clean is None:
                continue
            if clean != i.item_name:
                i.item_name = clean
        kept.append(i)
    indicators = kept
    from app.core.indicator_groups import group_indicators
    indicator_dicts = [
        {"item_name": i.item_name, "item_name_standard": i.item_name_standard,
         "item_code": i.item_code, "result_value": i.result_value,
         "unit": i.unit, "ref_range_low": i.ref_range_low,
         "ref_range_high": i.ref_range_high, "category": i.category}
        for i in indicators
    ]
    grouped, module_order = group_indicators(indicator_dicts)
    return {
        "id": report.id, "task_id": report.task_id,
        "name": display_name, "gender": report.gender, "age": report.age,
        "report_date": report.report_date, "check_type": report.check_type,
        "unit_name": report.unit_name, "conclusion_text": report.conclusion_text,
        "indicators": grouped,
        "module_order": module_order,
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
    from app.modules.followup.service import delete_report_followup
    delete_report_followup(db, report_id)
    if report.task_id:
        db.execute(text("DELETE FROM report_task WHERE id = :tid"), {"tid": report.task_id})
    db.delete(report)
    db.commit()
    return {"status": "deleted"}
