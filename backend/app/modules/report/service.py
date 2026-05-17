import base64
import os
from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session

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

        report = ReportInfo(
            task_id=task.id, user_id=task.user_id,
            name=personal_info.get("name"),
            gender=personal_info.get("gender"),
            age=personal_info.get("age"),
            report_date=personal_info.get("check_date"),
            check_type=personal_info.get("check_type"),
            unit_name=personal_info.get("unit_name"),
        )
        db.add(report)
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
    q = db.query(ReportInfo)
    if user_id:
        q = q.filter(ReportInfo.user_id == user_id)
    total = q.count()
    items = q.order_by(ReportInfo.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def get_report_detail(db: Session, report_id: int) -> Optional[ReportInfo]:
    return db.query(ReportInfo).filter(ReportInfo.id == report_id).first()


def get_report_indicators(db: Session, report_id: int) -> List[ReportIndicator]:
    return db.query(ReportIndicator).filter(ReportIndicator.report_id == report_id).all()
