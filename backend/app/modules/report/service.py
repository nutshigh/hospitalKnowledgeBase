import asyncio
import base64
import logging
import os
import re
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
- item_name: 该条问题的**异常发现**名称，直接取自文本，**必须**包含解剖部位：
  * "甲状腺双叶多发囊性结节" → "甲状腺双叶多发囊性结节"
  * "肝内钙化灶0.5cm" → "肝内钙化灶"
  * "右肺尖间隔旁型肺气肿" → "右肺尖间隔旁型肺气肿"
  * "室上性早搏" → "室上性早搏"
  * 去掉测量细节（如"大者0.3cm×0.2cm""TI-RADS 2 级""0.5cm"）和检查方法名
- item_normalized: 将异常发现标准化为一个标准医学名称，**必须**保留解剖部位前缀：
  * "甲状腺双叶多发囊性结节" → "甲状腺囊性结节"（不能只输出"囊性结节"）
  * "肝内钙化灶" → "肝内钙化灶"（不能只输出"钙化灶"）
  * "右肺尖间隔旁型肺气肿" → "右肺尖间隔旁型肺气肿"（不能只输出"肺气肿"）
  * 规则：原文提到哪个器官/部位，标准化名就必须带上该部位
  * 注意区分程度：体重指数超出正常范围但未达肥胖 → "超重"（不是"肥胖"），BMI≥28 可判为"肥胖"

- suggestion: 对应的建议原文，保留完整措辞
- deviation: 偏离方向，取值为"偏高""偏低""偏大""偏小""偏重""偏轻""异常"，解析不到则为null
- is_urgent: 如果建议中含"立即就医""尽快就诊""急诊""马上"等紧急关键词则为true，否则false

注意事项：
- 每一条编号对应的内容视为一个异常项，不要把多条合并
- **逐条核对，不允许遗漏**：输出前必须逐条对照输入文本的每一条编号，确保每条编号都对应一个输出项，缺少任何一条都必须补上，绝不能漏掉任意一条
- 只输出 JSON 数组，不要加任何说明或 Markdown 代码块
- 如果没有任何异常项，输出空数组[]

结论文本：
{text}"""


async def _extract_abnormalities_async(conclusion_text: str) -> list[dict]:
    """调用 MedGo LLM 从结论文本提取异常项列表。"""
    from app.ai.llm import get_chat_model, _guarded
    import json as _json, re as _re

    # 预处理1：合并 PDF 导致的折行，同一编号下的行拼成一段
    text = _merge_wrapped_lines(conclusion_text)
    # 预处理2：冒号拆分——只保留冒号后的正文（检查项前缀不发给 LLM）
    text = _keep_body_after_colon(text)

    prompt = _ABNORMALITY_PROMPT.format(text=text[:8000])
    model = get_chat_model()

    async def _call():
        return await model.ainvoke([("user", prompt)], max_tokens=2048)

    try:
        resp = await _guarded(_call())
        content = resp.content.strip()
        content = _re.sub(r'<think>.*?</think>', '', content, flags=_re.DOTALL)
        content = content.replace('</think>', '').replace('<think>', '')
        content = _re.sub(r'```json\s*', '', content)
        content = _re.sub(r'```\s*', '', content)
        items = _json.loads(content)
        if isinstance(items, list):
            for item in items:
                item["item_name"] = _strip_check_prefix(item.get("item_name", ""))
            # === STRATEGY:v2026-08-03-prefix-recover 部位补全 ===
            # LLM 对 item_normalized 的扩展不稳定（同一条"钙化灶"有时扩展成"肝内钙化灶"）。
            # 补救：从冒号正文中查找包含 item_name 的更长片段（含解剖部位前缀），
            # 当 LLM 未主动扩展时用它补全 item_name（纯确定性，无词表）。
            # 回退: 删除此 for 循环即可
            for item in items:
                item_name = item.get("item_name", "")
                llm_norm = item.get("item_normalized") or ""
                if not (llm_norm and len(llm_norm) > len(item_name)):
                    enriched = _recover_anatomical_prefix(text, item_name)
                    if enriched and enriched != item_name:
                        item["item_name"] = enriched
                        _log.info("prefix-recover: %s -> %s", item_name, enriched)
            # === END STRATEGY ===
            return items
    except Exception as e:
        _log.warning("Failed to extract abnormalities: %s", e)
    return []


def _recover_anatomical_prefix(text: str, item_name: str) -> Optional[str]:
    """从冒号正文中查找包含 item_name 的更长片段。

    若正文中存在"部位词 + item_name"的连续片段（如正文"肝内钙化灶0.5cm"、
    item_name"钙化灶" → 返回"肝内钙化灶"），返回该片段；否则返回原 item_name。

    注意：不补"血/血清/血浆/全血"等化验前缀（如"血肌酸激酶" → 保持"肌酸激酶"）。
    """
    if not item_name:
        return item_name
    for line in text.split('\n'):
        # 去掉编号前缀（如"3、"）
        line = re.sub(r'^\d+[\u3001,.\uff09)]?\s*', '', line.strip())
        if not line or item_name not in line:
            continue
        # 尝试每个出现位置，取"部位词+item_name"的最长扩展
        for m in re.finditer(re.escape(item_name), line):
            start = m.start()
            # 向前扩展到最近的汉字边界（停止在全角逗号/句号/数字/括号/空格等）
            prefix_start = start
            while prefix_start > 0 and '\u4e00' <= line[prefix_start - 1] <= '\u9fa5':
                prefix_start -= 1
            prefix = line[prefix_start:start]
            # 只补器官/部位前缀（如"肝内"、"甲状腺双叶多发"），跳过化验前缀
            if prefix and len(prefix) >= 1:
                if re.fullmatch(r'[血血清浆全]+', prefix):
                    continue
                return line[prefix_start:m.end()]
    return item_name


def _keep_body_after_colon(text: str) -> str:
    """冒号拆分策略：检查项前缀（冒号前）是检查方法名，正文（冒号后）才是异常发现。
    只保留正文，避免 LLM 把检查项名（如"甲状腺B 超"）当作异常或干扰部位提取。

    规则（按句号切分，冒号为第二边界）：
    1. 只处理有编号前缀的行（如"2、肺结节"），无编号行（解释/数据/建议行）丢弃
    2. 去掉以"建议"开头的句子（那是建议，不是发现）
    3. 去掉以"未检"开头的句子（如"未检项目：便潜血"，是未检说明，不是发现）
    4. 句子含冒号 → 只保留冒号后的内容（正文）
    5. 句子无冒号 → 整句保留（如"外耳道耵聍"）
    """
    out = []
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r'^(\d+[\u3001,.\uff09)]?\s*)(.*)$', stripped)
        if not m:
            # 无编号前缀 → 解释/数据/建议行，丢弃
            continue
        num_prefix = m.group(1)
        rest = m.group(2)
        # 按句号/分号切成句子
        sentences = [s.strip() for s in re.split(r'[。；;]', rest) if s.strip()]
        kept = []
        for sent in sentences:
            # 建议句丢弃
            if sent.startswith('建议'):
                continue
            # 未检说明丢弃（如"未检项目：便潜血"）
            if sent.startswith('未检') or sent.startswith('未查') or sent.startswith('未做'):
                continue
            # 有冒号 → 只留冒号后正文；无冒号 → 整句
            if '：' in sent or ':' in sent:
                after = re.split(r'[：:]', sent, maxsplit=1)[1].strip()
                if after:
                    kept.append(after)
            else:
                kept.append(sent)
        if kept:
            out.append(num_prefix + ' '.join(kept))
    return '\n'.join(out)


def _merge_wrapped_lines(text: str) -> str:
    """合并 PDF 折行：同一编号下的连续行拼成一段，避免 LLM 把一条结论拆成多条。

    编号行含冒号（如"3、甲状腺B 超：..."）→ 后续行是折行/建议，合并到该行。
    编号行无冒号（如"2、肺结节"）→ 该行是发现名，后续行是解释段落，不合并。
    """
    lines = text.split('\n')
    out = []
    buf = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buf:
                out.append(' '.join(buf))
                buf = []
            continue
        if re.match(r'^\d+[\u3001,.\uff09)]?\s*', stripped):
            if buf:
                out.append(' '.join(buf))
            if '：' in stripped or ':' in stripped:
                buf = [stripped]
            else:
                out.append(stripped)
                buf = []
        else:
            buf.append(stripped)
    if buf:
        out.append(' '.join(buf))
    return '\n'.join(out)


_CHECK_PREFIX_RE = re.compile(
    r'^[^：:，,。]*?(?:B\s*超|CT\s*平扫|CT\s*检查|X\s*光|X\s*线|MRI|超声|心电图|'
    r'人体代谢率|人体成分|骨密度|动脉硬化|经颅多普勒|'
    r'检查|检测|测定)\s*[：:]\s*'
)


def _strip_check_prefix(item_name: str) -> str:
    """剥离 LLM 可能误保留的检查项前缀，如 '甲状腺B 超：甲状腺双叶多发囊性结节' → '甲状腺双叶多发囊性结节'。"""
    m = _CHECK_PREFIX_RE.match(item_name)
    if m:
        return item_name[m.end():]
    return item_name


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
        # === STRATEGY:v2026-07-30-colon 冒号策略配套 ===
        # 把 LLM 的 item_normalized 传给 _normalize_abnormality：
        # 自动插入新条目时，若 LLM 扩展了名称（更长），用 LLM 的长名建条目。
        # 回退: 传 None 即可（即 db_normalized = _normalize_abnormality(db, item_name)）
        db_normalized = _normalize_abnormality(db, item_name, llm_normalized)
        # === END STRATEGY ===

        # === STRATEGY:v2026-07-30-REVERTED 已回退到旧策略 ===
        # 旧策略: db_normalized or llm_normalized or item_name
        # (曾尝试"LLM 扩展名优先"策略 v2026-07-30, 因 LLM 输出不稳定已回退)
        normalized = db_normalized or llm_normalized or item_name
        # === END STRATEGY ===

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
            source="conclusion",
            explanation=item_name,
            suggestion=suggestion,
        )
        db.add(ij)

    # 结论型内部去重：同一 interpretation 内新存储的结论项互相比对，模糊匹配则统一 disease_mapping
    if len(abnormalities) > 1:
        from app.modules.interpretation.service import _fuzzy_overlap, _link_disease_mapping
        for i in range(len(abnormalities)):
            for j in range(i + 1, len(abnormalities)):
                a_name = abnormalities[i].get("item_name", "")
                b_name = abnormalities[j].get("item_name", "")
                if a_name and b_name and _fuzzy_overlap(a_name, b_name):
                    _link_disease_mapping(db, a_name, b_name)

    db.commit()
    _log.info("abnormalities stored report=%d interp=%d count=%d",
              report_id, interpretation_id, len(abnormalities))


def _normalize_abnormality(db, item_name: str, llm_normalized: Optional[str] = None) -> Optional[str]:
    """查 disease_mapping 表，返回标准化 disease_name。
    优先精确匹配，其次用 _fuzzy_overlap 模糊匹配已有条目（避免创建重复映射）。
    找不到时自动创建新条目。

    === STRATEGY:v2026-07-30-colon 冒号策略配套 ===
    llm_normalized: LLM 的 item_normalized。自动插入新条目时，若 LLM 扩展了名称
    （llm_normalized 比 item_name 长，如 "甲状腺囊性结节" > "囊性结节"），
    用 LLM 的长名建条目，避免新库首次遇到该词时建成短名。
    回退: 调用方传 None 即恢复旧行为。
    === END STRATEGY ===
    """
    from sqlalchemy import text
    from app.modules.interpretation.service import _fuzzy_overlap
    import re as _re

    # === STRATEGY:v2026-08-04-nospace 去空格 ===
    # 与 term_normalizer 保持一致：标准化名统一去空格（"腹部B 超"→"腹部B超"）
    item_name = item_name.replace(" ", "").replace("　", "")
    core = _re.sub(r'(偏高|偏低|偏大|偏小|偏重|偏轻|异常|检查|显示|可见)+$', '', item_name).strip()
    if not core:
        return None

    try:
        # 1. 精确匹配
        row = db.execute(text(
            "SELECT id, item_name_standard, disease_name FROM disease_mapping WHERE enabled=1 AND (item_name_standard=:exact OR disease_name=:exact) LIMIT 1"
        ), {"exact": core}).fetchone()
        if row:
            return row[2]

        # 2. 前缀剥离后匹配
        core_stripped = _re.sub(r'^(血|血清|血浆|全血)', '', core).strip()
        if core_stripped != core:
            row = db.execute(text(
                "SELECT disease_name FROM disease_mapping WHERE enabled=1 AND (item_name_standard=:cs OR disease_name=:cs) LIMIT 1"
            ), {"cs": core_stripped}).fetchone()
            if row:
                return row[0]

        # 3. _fuzzy_overlap 模糊匹配已有条目
        candidates = db.execute(text(
            "SELECT id, item_name_standard, disease_name FROM disease_mapping WHERE enabled=1 ORDER BY id"
        )).fetchall()
        best = None
        best_len = 999
        for cid, cstd, cdn in candidates:
            if _fuzzy_overlap(core, cstd) or _fuzzy_overlap(core, cdn) or \
               (core_stripped and (_fuzzy_overlap(core_stripped, cstd) or _fuzzy_overlap(core_stripped, cdn))):
                if len(cstd) < best_len:
                    best = cdn
                    best_len = len(cstd)
        if best:
            return best

        # 4. 未找到 → 创建新映射
        # === STRATEGY:v2026-07-30-colon ===
        # 自动插入用 LLM 扩展名（若更长），否则用剥前缀后的短名
        if llm_normalized and len(llm_normalized) > len(item_name):
            disease = llm_normalized
        else:
            disease = core_stripped if core_stripped else core
        # === END STRATEGY ===
        db.execute(text(
            "INSERT INTO disease_mapping (item_name_standard, item_name, disease_name, disease_category, sort_code) "
            "VALUES (:std, :orig, :dn, 'OTHER', 200) "
            "ON DUPLICATE KEY UPDATE disease_name=VALUES(disease_name)"
        ), {"std": disease, "orig": item_name, "dn": disease})
        db.commit()
        return disease
    except Exception:
        pass
    return None


def _clean_unit_name(unit_name: Optional[str]) -> Optional[str]:
    """剥离机构名后缀，只保留医院名。
    规则：去掉"健康管理中心""体检中心""医疗中心"等后缀。
    "北京医院健康管理中心" → "北京医院"；"北京友谊医院" 不变。
    """
    if not unit_name:
        return None
    import re
    cleaned = re.sub(
        r'(健康管理中心|健康管理部|健康管理|体检中心|体检部|医疗中心|医院集团|门诊部|有限公司)+$',
        '', unit_name.strip(),
    ).strip()
    return cleaned or None


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
                "unit_name": parsed.get("unit_name"),
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
        # LLM 路径返回 report_date，VLM 路径返回 check_date
        report.report_date = personal_info.get("check_date") or personal_info.get("report_date")
        # report.check_type = personal_info.get("check_type")
        # === STRATEGY:v2026-08-04-unitname 提取体检机构名 ===
        # 从解析结果写入机构名（LLM/VLM 均已在 prompt/正则中支持提取）
        report.unit_name = _clean_unit_name(personal_info.get("unit_name"))
        # === END STRATEGY ===
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
  "unit_name": "体检机构名称（如XX医院、XX医院健康管理中心）或null",
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
6. **排除"总检建议与结论"段落**：不要提取"总检建议与结论""总检结论""医师建议""健康指导"等结论段落中的任何内容——那里的"XXX偏高/偏低"是结论文本，不是化验指标。真正的化验指标必须有检测数值（数字）和参考范围，或出现在化验数据表中
7. unit_name 从"XX医院""XX医院健康管理中心""XX体检中心"等提取机构名，只要医院名（如"xxx医院"），不要"健康管理中心"后缀；找不到填 null
8. 没有的字段填 null

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
