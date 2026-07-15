import asyncio
import base64
import os
from datetime import datetime, timezone
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
                thumbnail_path: Optional[str] = None,
                priority: str = "normal",
                batch_id: Optional[str] = None,
                file_id: Optional[str] = None) -> ReportTask:
    # 向后兼容: legacy int priority(0=normal, 1=urgent)
    if isinstance(priority, int):
        priority = "urgent" if priority else "normal"
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

    payload = {"task_id": task.id, "hospital_id": hospital_id, "file_path": file_path}
    if batch_id is not None:
        payload["batch_id"] = batch_id
    if file_id is not None:
        payload["file_id"] = file_id
    rabbitmq.publish(TaskMessage(
        task_type="parsing", hospital_id=hospital_id, priority=priority,
        payload=payload,
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

        # For text-based PDFs, use direct text extraction + LLM parsing
        if task.file_type == "pdf" and _pdf_has_text(processed_path):
            text = _extract_pdf_text(processed_path)
            parsed = _parse_text_with_llm(text)
            personal_info = {
                "name": parsed.get("name"),
                "gender": parsed.get("gender"),
                "age": parsed.get("age"),
                "check_date": parsed.get("report_date"),
            }
            # LLM already returns ref_low/ref_high — normalize names
            raw_indicators = parsed.get("indicators", [])
            indicators = normalize_indicators([
                {
                    "item_name": ind.get("item_name", ""),
                    "result": ind.get("result", ""),
                    "unit": ind.get("unit", ""),
                    "ref_low": ind.get("ref_low"),
                    "ref_high": ind.get("ref_high"),
                }
                for ind in raw_indicators
            ])
        else:
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
        # report.check_type = personal_info.get("check_type")
        # report.unit_name = personal_info.get("unit_name")
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
        task.completed_at = datetime.now(timezone.utc)
        db.commit()

        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=task.priority,
            payload={"report_id": report.id, "hospital_id": hospital_id},
        ))

    except Exception as e:
        task.retry_count += 1
        task.error_message = str(e)
        if task.retry_count >= 3:
            task.status = "failed"
        else:
            task.status = "queued"
            db.commit()
            rabbitmq.publish(TaskMessage(
                task_type="parsing", hospital_id=hospital_id, priority=task.priority,
                payload={"task_id": task.id, "hospital_id": hospital_id, "file_path": task.original_file_path},
            ))
        db.commit()


def _pdf_has_text(file_path: str) -> bool:
    """Check if PDF has enough embedded text for direct extraction."""
    try:
        import fitz
        doc = fitz.open(file_path)
        total = sum(len(page.get_text().strip()) for page in doc)
        doc.close()
        return total > 200  # 200+ chars → text-based PDF
    except Exception:
        return False


def _extract_pdf_text(file_path: str) -> str:
    """Extract all text from a text-based PDF."""
    import fitz
    doc = fitz.open(file_path)
    texts = []
    for i, page in enumerate(doc):
        t = page.get_text().strip()
        if t:
            texts.append(f"--- Page {i+1} ---\n{t}")
    doc.close()
    return "\n\n".join(texts)


def _parse_text_with_llm(text: str) -> dict:
    """Send extracted PDF text to LLM for indicator parsing."""
    return asyncio.run(_parse_text_with_llm_async(text))


async def _parse_text_with_llm_async(text: str) -> dict:
    """实际 async 解析，包裹在 medgo_sem 内。"""
    from app.ai.llm import get_chat_model, _guarded
    prompt = _build_parse_prompt(text)
    model = get_chat_model()

    async def _call():
        return await model.ainvoke([("user", prompt)], max_tokens=16384)

    resp = (await _guarded(_call())).content
    return _parse_llm_json(resp)


def _build_parse_prompt(text: str) -> str:
    return f"""从以下体检报告文本中提取信息，返回 JSON 格式（不要 Markdown 代码块）：

{{
  "name": "姓名",
  "gender": "男或女",
  "age": 年龄数字或null,
  "report_date": "YYYY-MM-DD或null",
  "indicators": [
    {{"item_name": "指标名称", "result": "检测结果", "unit": "单位", "ref_low": "参考下限", "ref_high": "参考上限"}}
  ]
}}

规则：
1. 姓名从"尊敬的XXX先生/女士"或"姓名:XXX"提取
2. 性别："先生"→男，"女士"→女
3. 年龄：从"XX岁"提取数字
4. 参考范围如"3.5-9.5"→ref_low="3.5", ref_high="9.5"；如"<5.0"→ref_low="", ref_high="5.0"
5. 只提取化验指标数据（血常规、生化、免疫等），不提取问卷、个人信息
6. 没有的字段填 null

体检报告文本：
{text[:24000]}
"""


def _parse_llm_json(resp: str) -> dict:
    import json, re
    from json_repair import repair_json
    match = re.search(r'\{[\s\S]*\}', resp)
    if not match:
        raise ValueError(f"LLM did not return valid JSON: {resp[:200]}")
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        data = json.loads(repair_json(match.group()))
    for ind in data.get("indicators", []):
        ref = ind.pop("ref_range", None)
        if ref and "ref_low" not in ind:
            from app.core.vlm_client import _parse_ref_range
            lo, hi = _parse_ref_range(str(ref))
            ind["ref_low"] = lo
            ind["ref_high"] = hi
    return data


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
    # Attach interpretation status (latest report_interpretation per report)
    report_ids = [r.id for r in items]
    if report_ids:
        from app.modules.interpretation.models import ReportInterpretation
        interps = {}
        for ri in db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id.in_(report_ids)
        ).order_by(ReportInterpretation.id.desc()).all():
            interps.setdefault(ri.report_id, ri)
    else:
        interps = {}
    results = []
    for r in items:
        task = tasks.get(r.task_id)
        interp = interps.get(r.id)
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
            "interp_status": interp.status if interp else None,
            "overall_level": interp.overall_level if interp else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return results, total


def get_report_detail(db: Session, report_id: int) -> Optional[ReportInfo]:
    return db.query(ReportInfo).filter(ReportInfo.id == report_id).first()


def get_report_indicators(db: Session, report_id: int) -> List[ReportIndicator]:
    return db.query(ReportIndicator).filter(ReportIndicator.report_id == report_id).all()
