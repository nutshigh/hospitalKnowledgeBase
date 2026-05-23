import base64
import os
from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator
from app.core.vlm_client import vlm_client
from app.core.term_normalizer import normalize_indicators
from app.core.image_preprocess import preprocess
from app.core.rabbitmq import rabbitmq, TaskMessage


def create_task(db: Session, hospital_id: str, user_id: int, file_path: str,
                filename: str, file_type: str, file_size: int,
                thumbnail_path: Optional[str] = None, priority: int = 0) -> ReportTask:
    task = ReportTask(
        user_id=user_id, original_file_path=file_path, original_filename=filename,
        file_type=file_type, file_size=file_size, thumbnail_path=thumbnail_path,
        status="queued", priority=priority,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # Create report_info immediately so it appears on home page
    report = ReportInfo(task_id=task.id, user_id=user_id)
    db.add(report)
    db.commit()

    rabbitmq.publish(TaskMessage(
        task_type="parsing", hospital_id=hospital_id, priority=priority,
        payload={"task_id": task.id, "hospital_id": hospital_id, "file_path": file_path},
    ))
    return task


def get_task_status(db: Session, task_id: int) -> Optional[ReportTask]:
    return db.query(ReportTask).filter(ReportTask.id == task_id).first()


def process_task(db: Session, task_id: int, hospital_id: str):
    task = get_task_status(db, task_id)
    if not task:
        return

    task.status = "parsing"
    db.commit()

    try:
        user_dir = os.path.dirname(task.original_file_path)

        if task.file_type == "image":
            processed_path, error_msg = preprocess(task.original_file_path, user_dir)
            if error_msg:
                task.status = "failed"
                task.error_message = error_msg
                db.commit()
                return
        else:
            processed_path = task.original_file_path

        images_b64 = _file_to_base64_list(processed_path, task.file_type)
        result = vlm_client.extract_from_images(images_b64)
        indicators = normalize_indicators(result.get("indicators", []))
        personal_info = result.get("personal_info", {})

        # Update existing report_info (created in create_task), or create if missing
        report = db.query(ReportInfo).filter(ReportInfo.task_id == task.id).first()
        if not report:
            report = ReportInfo(task_id=task.id, user_id=task.user_id)
            db.add(report)
        report.name = personal_info.get("name")
        report.gender = personal_info.get("gender")
        report.age = personal_info.get("age")
        report.report_date = personal_info.get("check_date")
        report.check_type = personal_info.get("check_type")
        report.unit_name = personal_info.get("unit_name")
        db.commit()
        db.refresh(report)

        for ind in indicators:
            db.add(ReportIndicator(
                report_id=report.id,
                item_name=ind.get("item_name", ""),
                item_name_standard=ind.get("item_name_standard"),
                item_code=ind.get("item_code"),
                result_value=ind.get("result"),
                unit=ind.get("unit"),
                ref_range_low=ind.get("ref_low"),
                ref_range_high=ind.get("ref_high"),
                raw_text=ind.get("raw_text"),
            ))
        db.commit()

        task.status = "completed"
        task.completed_at = datetime.utcnow()
        db.commit()

        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=task.priority,
            payload={"report_id": report.id, "hospital_id": hospital_id},
        ))

    except Exception as e:
        task.retry_count += 1
        task.status = "failed" if task.retry_count >= 3 else "queued"
        task.error_message = str(e)
        db.commit()


def _file_to_base64_list(file_path: str, file_type: str) -> list[str]:
    if file_type == "image":
        with open(file_path, "rb") as f:
            return [base64.b64encode(f.read()).decode()]
    elif file_type == "pdf":
        import fitz
        doc = fitz.open(file_path)
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            images.append(base64.b64encode(pix.tobytes("jpg")).decode())
        doc.close()
        return images
    elif file_type == "docx":
        raise ValueError("DOCX parsing not yet supported via VLM — use text extraction instead")
    else:
        raise ValueError(f"Cannot convert file_type={file_type} to images")


def list_reports(db: Session, hospital_id: str, user_id: Optional[int] = None,
                 page: int = 1, page_size: int = 20) -> tuple:
    from sqlalchemy.orm import joinedload
    q = db.query(ReportInfo)
    if user_id:
        q = q.filter(ReportInfo.user_id == user_id)
    total = q.count()
    items = q.order_by(ReportInfo.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    # Attach task status to each report
    task_ids = [r.task_id for r in items if r.task_id]
    if task_ids:
        tasks = {t.id: t for t in db.query(ReportTask).filter(ReportTask.id.in_(task_ids)).all()}
    else:
        tasks = {}
    results = []
    for r in items:
        task = tasks.get(r.task_id)
        results.append({
            "id": r.id,
            "task_id": r.task_id,
            "name": r.name,
            "gender": r.gender,
            "age": r.age,
            "report_date": r.report_date,
            "check_type": r.check_type,
            "unit_name": r.unit_name,
            "task_status": task.status if task else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return results, total


def get_report_detail(db: Session, report_id: int) -> Optional[ReportInfo]:
    return db.query(ReportInfo).filter(ReportInfo.id == report_id).first()


def get_report_indicators(db: Session, report_id: int) -> List[ReportIndicator]:
    return db.query(ReportIndicator).filter(ReportIndicator.report_id == report_id).all()
