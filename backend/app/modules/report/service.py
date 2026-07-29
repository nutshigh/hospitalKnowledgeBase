import asyncio
import base64
import logging
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

_log = logging.getLogger("app.parse")


def create_task(db: Session, hospital_id: str, user_id: int, file_path: str,
                filename: str, file_type: str, file_size: int,
                thumbnail_path: Optional[str] = None,
                priority: str = "normal",
                batch_id: Optional[str] = None,
                file_id: Optional[str] = None) -> ReportTask:
    # 向后兼容: legacy int priority(0=normal, 1=urgent)
    if isinstance(priority, int):
        priority = "urgent" if priority else "normal"
    # DB 列 priority 是 Integer(BIGINT),不能存字符串;按 str→int 映射落库
    priority_for_db = {"normal": 0, "urgent": 1, "bulk": 100}[priority]
    task = ReportTask(
        user_id=user_id, original_file_path=file_path, original_filename=filename,
        file_type=file_type, file_size=file_size, thumbnail_path=thumbnail_path,
        status="queued", priority=priority_for_db,
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


_CONCLUSION_PROMPT = """以下是一份体检报告的完整文本。请提取其中"总检建议与结论"段落的全部内容。

提示：该段落通常以"总检建议与结论""总检结论""医师建议""综合建议""健康指导"等标题开头，以"主检医师""主检医生""总检医师""总检医生""一般项目""一般检查""检查项目"等标识结束。

请只输出提取到的内容原文（包含章节标题），不要加任何说明。如果确实找不到，输出NONE。

报告文本：
{text}"""


def _clean_conclusion(content: str) -> Optional[str]:
    """清理 LLM 返回的结论文本，去除 think 标签、截断主检医生部分、空响应。"""
    import re
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    content = content.replace('</think>', '').replace('<think>', '')
    # 截断主检医生/总检医生之后的内容
    content = re.split(r'(?:主检医生|总检医生|主检医师|总检医师)\s*[：:]', content)[0]
    # 移除开头的章节标题重复
    content = re.sub(r'^(总检建议与结论|总检结论|医师建议|综合建议|健康指导)\s*\n+', '', content)
    content = content.strip()
    if not content or content.upper() in ("NONE", "(无)", "无", "NULL"):
        return None
    if len(content) < 10:
        return None
    return content


async def _extract_conclusion_async(text: str) -> Optional[str]:
    """调用 MedGo LLM 提取报告结论段落。"""
    from app.ai.llm import get_chat_model, _guarded

    prompt = _CONCLUSION_PROMPT.format(text=text[:16000])
    model = get_chat_model()

    async def _call():
        return await model.ainvoke([("user", prompt)], max_tokens=2048)

    try:
        resp = await _guarded(_call())
        conclusion = _clean_conclusion(resp.content)
        if conclusion:
            return conclusion
    except Exception as e:
        _log.warning("Failed to extract conclusion: %s", e)
    return None


_ABNORMALITY_PROMPT = """请从以下体检报告的"总检建议与结论"文本中，逐条提取所有异常项。

每条异常对应一个 JSON 对象，包含以下字段：
- item_name: 结论中该条问题的完整原文描述（如"血肌酸激酶偏高，同型半胱氨酸偏高"）
- item_normalized: 将该项问题标准化为一个标准医学名称，**必须**保留解剖部位前缀：
  * "甲状腺双叶多发囊性结节，TI-RADS 2级" → 标准化为 "甲状腺囊性结节"（不能只输出"囊性结节"）
  * "肝内钙化灶0.5cm" → 标准化为 "肝内钙化灶"（不能只输出"钙化灶"）
  * "右肺尖间隔旁型肺气肿" → 标准化为 "肺气肿"（双肺通用可省位置）
  * 规则：结论文本中异常名称前提到了哪个器官/部位，标准化名就必须带上
  * 注意区分程度：体重指数超出正常范围但未达肥胖 → "超重"（不是"肥胖"），BMI≥28 可判为"肥胖"
  * 避免过度泛化，尊重原文具体描述

- suggestion: 对应的建议原文，保留完整措辞
- deviation: 偏离方向，取值为"偏高""偏低""偏大""偏小""偏重""偏轻""异常"，解析不到则为null
- is_urgent: 如果建议中含"立即就医""尽快就诊""急诊""马上"等紧急关键词则为true，否则false

注意事项：
- 每一条编号对应的内容视为一个异常项，不要把多条合并
- 保留原文措辞，不要缩写或改写
- 只输出 JSON 数组，不要加任何说明或 Markdown 代码块
- 如果没有任何异常项，输出空数组[]

结论文本：
{text}"""


async def _extract_abnormalities_async(conclusion_text: str) -> list[dict]:
    """调用 MedGo LLM 从结论文本提取异常项列表。"""
    from app.ai.llm import get_chat_model, _guarded
    import json as _json, re as _re

    prompt = _ABNORMALITY_PROMPT.format(text=conclusion_text[:8000])
    model = get_chat_model()

    async def _call():
        return await model.ainvoke([("user", prompt)], max_tokens=2048)

    try:
        resp = await _guarded(_call())
        content = resp.content.strip()
        # 去掉可能的 <think> 标签和 Markdown 代码块
        content = _re.sub(r'<think>.*?</think>', '', content, flags=_re.DOTALL)
        content = content.replace('</think>', '').replace('<think>', '')
        content = _re.sub(r'```json\s*', '', content)
        content = _re.sub(r'```\s*', '', content)
        items = _json.loads(content)
        if isinstance(items, list):
            return items
    except Exception as e:
        _log.warning("Failed to extract abnormalities: %s", e)
    return []


def _store_abnormalities(db, report_id: int, interpretation_id: int,
                         abnormalities: list[dict]) -> None:
    """将结论提取的异常项写入 report_indicator + indicator_judgment。

    每次解析时检查 interpretation_id 是否已有结论型异常，有则跳过（增量追加，不重复）。
    """
    from app.modules.report.models import ReportIndicator
    from app.modules.interpretation.models import IndicatorJudgment
    from sqlalchemy import text

    if not abnormalities:
        return

    # 检查是否已存在该 interpretation 的结论型异常（通过 report_indicator.raw_text 非空来识别）
    existing = db.execute(
        text("""SELECT COUNT(*) FROM indicator_judgment ij
                JOIN report_indicator ri ON ij.indicator_id = ri.id
                WHERE ij.interpretation_id = :iid AND ri.raw_text IS NOT NULL"""),
        {"iid": interpretation_id},
    ).scalar()
    if existing:
        _log.debug("abnormalities already stored for interp=%d, skip", interpretation_id)
        return

    for item in abnormalities:
        item_name = (item.get("item_name") or "").strip()
        suggestion = (item.get("suggestion") or "").strip()
        deviation = item.get("deviation")
        is_urgent = item.get("is_urgent", False)

        if not item_name and not suggestion:
            continue

        # 标准化名仅用于 disease_mapping 链接，不覆盖 item_name
        llm_normalized = (item.get("item_normalized") or "").strip()
        db_normalized = _normalize_abnormality(db, item_name)
        normalized = db_normalized or llm_normalized or item_name

        # 同一 interpretation 内去重（按归一化名）
        dup = db.execute(
            text("""SELECT COUNT(*) FROM indicator_judgment ij
                    JOIN report_indicator ri ON ij.indicator_id = ri.id
                    WHERE ij.interpretation_id = :iid AND ij.item_name = :nm AND ri.raw_text IS NOT NULL"""),
            {"iid": interpretation_id, "nm": item_name},
        ).scalar()
        if dup:
            _log.debug("abnormality dup skip interp=%d item=%s", interpretation_id, normalized)
            continue

        # 创建 report_indicator 占位行：原文名存储，标准化名存 item_name_standard
        ri = ReportIndicator(
            report_id=report_id,
            item_name=item_name,
            item_name_standard=normalized,
            raw_text=item_name,
        )
        db.add(ri)
        db.flush()  # 拿到 ri.id

        # 创建 indicator_judgment
        ij = IndicatorJudgment(
            interpretation_id=interpretation_id,
            indicator_id=ri.id,
            item_name=normalized or item_name,
            result_value=None,
            deviation=deviation if deviation else None,
            color_level="red" if is_urgent else "yellow",
            explanation=item_name,
            suggestion=suggestion,
        )
        db.add(ij)

    db.commit()
    _log.info("abnormalities stored report=%d interp=%d count=%d",
              report_id, interpretation_id, len(abnormalities))


def _normalize_abnormality(db, item_name: str) -> Optional[str]:
    """查 disease_mapping 表，返回标准化 disease_name。
    
    找不到映射时返回 None，由调用方使用 LLM 的 item_normalized。
    不再自动创建新映射，避免 LLM 每次的措辞差异产生噪音条目。
    """
    from sqlalchemy import text
    import re

    core = re.sub(r'(偏高|偏低|偏大|偏小|偏重|偏轻|异常|检查|显示|可见)+$', '', item_name).strip()
    if not core:
        return None

    try:
        # 精确匹配
        row = db.execute(text(
            "SELECT disease_name FROM disease_mapping WHERE enabled=1 AND item_name_standard=:exact LIMIT 1"
        ), {"exact": core}).scalar()
        if row:
            return row

        # 模糊匹配：core 包含 item_name_standard 或反之
        row = db.execute(text(
            "SELECT disease_name FROM disease_mapping WHERE enabled=1 AND (item_name_standard LIKE CONCAT('%', :core, '%') OR :core LIKE CONCAT('%', item_name_standard, '%')) ORDER BY CHAR_LENGTH(item_name_standard) LIMIT 1"
        ), {"core": core}).scalar()
        if row:
            return row

        # 去前缀后重试
        core_stripped = re.sub(r'^(血|血清|血浆|全血)', '', core).strip()
        if core_stripped != core:
            row = db.execute(text(
                "SELECT disease_name FROM disease_mapping WHERE enabled=1 AND item_name_standard=:cs LIMIT 1"
            ), {"cs": core_stripped}).scalar()
            if row:
                return row
    except Exception:
        pass
    return None


def process_task(db: Session, task_id: int, hospital_id: str,
                 batch_id: Optional[str] = None,
                 file_id: Optional[str] = None):
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

        report_raw_text = None
        images_b64 = None  # for VLM conclusion extraction on image-based reports

        # For text-based PDFs, use direct text extraction + LLM parsing
        if task.file_type == "pdf" and _pdf_has_text(processed_path):
            text = _extract_pdf_text(processed_path)
            report_raw_text = text
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
            report_raw_text = result.get("raw_text", "")

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

        # Extract conclusion text from report raw content
        if images_b64 is not None:
            # Image-based: use VLM to extract conclusion directly from images
            try:
                conclusion = vlm_client.extract_conclusion_from_images(images_b64)
                if conclusion:
                    report.conclusion_text = conclusion
                    db.commit()
                    _log.info("conclusion extracted via VLM report=%d len=%d", report.id, len(conclusion))
            except Exception as e:
                _log.warning("VLM conclusion extraction failed report=%d: %s", report.id, e)
        elif report_raw_text and len(report_raw_text) > 100:
            # Text-based PDF: use LLM on extracted full text
            try:
                conclusion = asyncio.run(_extract_conclusion_async(report_raw_text))
                if conclusion:
                    report.conclusion_text = conclusion
                    db.commit()
                    _log.info("conclusion extracted via LLM report=%d len=%d", report.id, len(conclusion))
            except Exception as e:
                _log.warning("LLM conclusion extraction failed report=%d: %s", report.id, e)

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

        # task.priority 已是 DB int (0/1/100),TaskMessage 需要 str priority 路由
        publish_priority = {0: "normal", 1: "urgent", 100: "bulk"}.get(task.priority or 0, "normal")
        payload = {"report_id": report.id, "hospital_id": hospital_id}
        if batch_id is not None:
            payload["batch_id"] = batch_id
        if file_id is not None:
            payload["file_id"] = file_id
        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=publish_priority,
            payload=payload,
        ))

    except Exception as e:
        task.retry_count += 1
        task.error_message = str(e)
        if task.retry_count >= 3:
            task.status = "failed"
        else:
            task.status = "queued"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
        # 重试决策交给 worker(走 publish_retry 延迟)
        raise


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
