import logging
import re
from typing import Optional
from httpx import Client, Timeout
from app.config import settings

logger = logging.getLogger(__name__)

OCR_PROMPT = """<image>\n<|grounding|>Extract all lab test indicators from this medical report as a Markdown table.

## Personal Info
**Name:** <patient name>
**Gender:** <male/female>
**Age:** <number>
**Date:** <exam date>

## Indicators Table
| 项目名称 | 结果 | 单位 | 参考范围低 | 参考范围高 |
| --- | --- | --- | --- | --- |
| (each indicator) | (result value) | (unit) | (ref_low) | (ref_high) |

Rules:
1. Reference range "3.5-9.5": split into ref_low="3.5", ref_high="9.5"
2. "<5.0": ref_high="5.0", ref_low empty
3. ">1.0": ref_low="1.0", ref_high empty
4. Null fields: leave cell empty
5. Output EXACTLY one indicators table, do NOT repeat rows
6. Keep result values exactly as shown in the report"""


def _clean_markdown(text: str) -> str:
    """Strip special tokens and truncate hallucinated repetition."""
    text = text.replace("<｜end▁of▁sentence｜>", "")
    text = re.sub(r"<\|ref\|>.*?<\|/ref\|><\|det\|>.*?<\|/det\|>", "", text)
    text = re.sub(r"<\|ref\|>.*?<\|/ref\|>", "", text)
    text = re.sub(r"<\|det\|>.*?<\|/det\|>", "", text)
    text = text.replace("\\coloneqq", ":=").replace("\\eqqcolon", "=:")
    # Truncate at first sign of hallucination (repeated non-table lines)
    lines = text.split("\n")
    clean_lines = []
    repeat_count = 0
    for line in lines:
        stripped = line.strip()
        # Detect repeated non-table content
        if stripped and not stripped.startswith("|"):
            if clean_lines and stripped == clean_lines[-1].strip():
                repeat_count += 1
                if repeat_count >= 3:
                    break
            else:
                repeat_count = 0
        elif stripped.startswith("|"):
            repeat_count = 0
        clean_lines.append(line)
    text = "\n".join(clean_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_personal_info(text: str) -> dict:
    """Extract personal info from Markdown/text lines like **Key:** value or Key: value."""
    info = {}
    patterns = {
        "name": r"(?:\*\*Name:\*\*|Name)\s*[:：]\s*(.+?)(?:\s+(?:Gender|性\s*别)|$)",
        "gender": r"(?:\*\*Gender:\*\*|Gender)\s*[:：]\s*(.+?)(?:\s+(?:Age|年\s*龄)|$)",
        "age": r"(?:\*\*Age:\*\*|Age)\s*[:：]\s*(\d+)",
        "check_date": r"(?:\*\*Date:\*\*|Date)\s*[:：]\s*(.+?)(?:\n|$)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and val.lower() not in ("<patient name>", "<male/female>", "<number>", "<exam date>", "null", "none"):
                info[key] = val
    return info


def _parse_personal_info_cn(text: str) -> dict:
    """从中文体检报告 markdown 文本提取个人信息。

    匹配常见格式：姓名:XXX / 姓名 XXX、性别:男、年龄:30岁、体检日期:2024-01-01、
    机构名:XX医院 / XX医院健康管理中心
    """
    info = {}
    patterns = {
        "name": r"姓\s*名[:：\s]+([^\s,，\|]{2,10})",
        "gender": r"性\s*别[:：\s]+(男|女)",
        "age": r"年\s*龄[:：\s]*(\d+)",
        "check_date": r"(?:体检日期|检查日期|日期|日\s*期)[:：\s]+(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})",
        # === STRATEGY:v2026-08-04-unitname 提取体检机构名 ===
        # 匹配 "XX医院" / "XX医院健康管理中心" / "XX体检中心"，取医院名部分
        "unit_name": r"([\u4e00-\u9fa5A-Za-z]{2,30}?(?:医院|体检中心|健康管理中心|中心医院))",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            val = m.group(1).strip()
            if val:
                info[key] = val
    # 机构名规范化：去掉"健康管理中心"等后缀，只保留医院名
    if info.get("unit_name"):
        unit = info["unit_name"]
        # "xxx医院健康管理中心" → "xxx医院"；"xxx体检中心" → "xxx体检中心"
        m2 = re.search(r"(.+?(?:医院|体检中心|中心医院))", unit)
        if m2:
            info["unit_name"] = m2.group(1)
    return info


def _html_table_rows(text: str) -> list[list[str]]:
    """从 PaddleOCR-VL 输出的 HTML <table> 提取行单元格(PaddleOCR-VL 默认输出 HTML 表格而非 markdown |)。"""
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", text, re.S):
        cells = []
        for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S):
            cells.append(re.sub(r"<[^>]+>", "", td).strip())
        rows.append(cells)
    return rows


def _parse_markdown_table(text: str) -> list[dict]:
    """Parse a Markdown table into a list of indicator dicts."""
    lines = text.split("\n")
    table_rows = []

    if "<tr>" in text:
        table_rows = _html_table_rows(text)
    else:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [c.strip() for c in stripped[1:-1].split("|")]
                if all(c.replace("-", "").replace(" ", "") == "" for c in cells):
                    continue  # separator row
                table_rows.append(cells)

    if len(table_rows) < 2:
        return []

    # 2026-08-26: 表头行不一定是第一行(钦州等报告的"模块标题行"先出现)
    # 找第一个能映射出字段的表头行; 其后的"检查人员/审核时间"类行跳过
    header_idx = None
    for i, row in enumerate(table_rows):
        if _match_header_columns(row):
            header_idx = i
            break
    if header_idx is None:
        return []

    # Map header columns
    header = table_rows[header_idx]
    col_map = _match_header_columns(header)

    indicators = []
    seen = set()
    for row in table_rows[header_idx + 1:]:
        if any(("人员" in c) or ("检查时间" in c) or ("审核时间" in c) or ("时间:" in c) for c in row):
            continue
        # 2026-08-26: 多表格报告(钦州)后续模块自带表头 → 切换列映射
        cm = _match_header_columns(row)
        if cm:
            col_map = cm
            continue
        indicator = _row_to_indicator(row, col_map)
        name = indicator.get("item_name", "").strip()
        if not name or name in ("(each indicator)", "项目名称", "结果"):
            continue
        # 2026-08-26: 页眉姓名行 / 问诊类行不入指标
        if re.search(r"(姓名[:：]|性别[:：]|门诊号|体检编号|既往病史|家族病史|月经史|"
                     r"遗传病史|过敏史|婚育史|手术史|输血史|神经及精神疾病)", name):
            continue
        # 2026-08-26: 无结果无参考的行(报告尾页医师/热线)不入指标
        if not indicator.get("result") and not (indicator.get("ref_low") or indicator.get("ref_high")):
            continue
        # Deduplicate: same name + same value → skip
        key = (name, indicator.get("result", ""))
        if key in seen:
            continue
        seen.add(key)
        # 参考范围解析: 支持 "3.5~9.5" / "~5.17"(仅上限) / "5.17~"(仅下限) / "<5.2"
        # 非数字参考(如"阴性")置 None, 避免脏数据落库
        for ref_key in ("ref_low", "ref_high"):
            val = indicator.get(ref_key)
            if val is None:
                continue
            sv = str(val)
            if not re.search(r"\d", sv):
                indicator[ref_key] = None
                continue
            lo, hi = _parse_ref_range(sv)
            if lo is not None or hi is not None:
                indicator["ref_low"] = lo
                indicator["ref_high"] = hi
        indicators.append(indicator)

    return indicators


def _match_header_columns(headers: list[str]) -> dict:
    """Match Chinese header keywords to standard field names."""
    keywords = {
        "item_name": ["项目名称", "检验项目", "项目", "指标名称", "检查项目", "测定项目"],
        "item_code": ["缩写", "英文简称", "代码", "代号", "缩写符号"],
        "result": ["结果", "测定值", "检验结果", "实测值", "检测结果", "数值"],
        "unit": ["单位", "计量单位"],
        "ref_low": ["参考范围低", "参考低", "下限"],
        "ref_high": ["参考范围高", "参考高", "上限"],
        # 2026-08-29: 提示/标志列(广西"提示"列: ↑/偏高/H 等报告方异常标志)
        "flag": ["提示", "标志", "结果提示"],
    }

    def _jaccard(a: str, b: str) -> float:
        sa, sb = set(a), set(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    mapping = {}
    for i, h in enumerate(headers):
        best_key, best_score = None, 0.0
        for key, aliases in keywords.items():
            for alias in aliases:
                score = _jaccard(h, alias)
                if score > best_score:
                    best_score = score
                    best_key = key
        if best_score > 0.3:
            mapping[i] = best_key

    return mapping


def _row_to_indicator(row: list[str], col_map: dict) -> dict:
    """Convert a table row to an indicator dict using column mapping."""
    indicator = {}
    # 2026-08-29: 任意列出现报告方异常标志(提示列值/错位列) → signal_flag=3
    # (钦州 YMII 行列错位, "↑"落在参考列; 统一按标志词扫描兜底)
    for cell in row:
        if re.match(r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|\\uparrow|\\downarrow)$", cell.strip()):
            indicator["signal_flag"] = 3
            break
    for i, cell in enumerate(row):
        key = col_map.get(i)
        if key is None:
            # Guess: number → result, contains "-" → ref_range
            if re.match(r"^[\d.]+$", cell) and "result" not in indicator:
                key = "result"
            elif re.match(r"^[<＞>\d].*[\d]|[～\-—].*[\d]", cell):
                if "ref_low" not in indicator:
                    low, high = _parse_ref_range(cell)
                    indicator["ref_low"] = low
                    indicator["ref_high"] = high
                continue
            else:
                continue

        if key == "item_name":
            indicator["item_name"] = cell
        elif key == "item_code":
            indicator["item_code"] = cell
        elif key == "result":
            indicator["result"] = cell
        elif key == "unit":
            cell = cell.strip()
            # 2026-08-26: 无单位行(体重指数等)参考范围可能被 VLM 对齐到单位列
            if re.search(r"\d[\d.]*\s*[~\-—～到至]\s*[\d.]+", cell) or re.match(r"[<>＜＞~～]\s*[\d.]+", cell):
                lo, hi = _parse_ref_range(cell)
                indicator["ref_low"] = lo
                indicator["ref_high"] = hi
            else:
                indicator["unit"] = cell
        elif key == "flag":
            # 2026-08-29: 提示/标志列 → 报告方异常标志(↑/↓/偏高/H/L/阳性/LaTeX \uparrow) → signal_flag=3
            cell = cell.strip()
            if re.match(r"^(偏高|升高|增高|↑|H|偏低|降低|↓|L|阳性|异常|\\uparrow|\\downarrow)$", cell):
                indicator["signal_flag"] = 3
        elif key == "ref_low":
            indicator["ref_low"] = cell or None
        elif key == "ref_high":
            indicator["ref_high"] = cell or None

    # If ref_low/ref_high not set by columns, try parsing combined ref_range cell
    return indicator


def _parse_ref_range(text: str) -> tuple:
    """Parse reference range string. "3.5-9.5" → ("3.5", "9.5")."""
    text = text.strip()
    m = re.match(r"([\d.]+)\s*[-~—到至]\s*([\d.]+)", text)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"[<＜]\s*([\d.]+)", text)
    if m:
        return None, m.group(1)
    m = re.match(r"[>＞]\s*([\d.]+)", text)
    if m:
        return m.group(1), None
    # 2026-08-26: 钦州单限格式 "~5.17"(仅有上限) / "5.17~"(仅有下限)
    m = re.match(r"[~～]\s*([\d.]+)$", text)
    if m:
        return None, m.group(1)
    m = re.match(r"([\d.]+)\s*[~～]$", text)
    if m:
        return m.group(1), None
    return None, None


class VLMClient:
    def __init__(self, base_url: str = settings.OCR_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.model = settings.OCR_MODEL
        custom_prompt = getattr(settings, "OCR_PROMPT", None)
        self.prompt = custom_prompt.strip() if custom_prompt else OCR_PROMPT
        self.client = Client(timeout=Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0))

    def extract_from_image(self, image_base64: str) -> dict:
        """调用 PaddleOCR-VL 服务（/ocr），返回 {personal_info, indicators, raw_text}。

        PaddleOCR-VL 输出页面级 markdown（含表格），复用现有
        _parse_markdown_table / _parse_personal_info 解析。
        """
        response = self.client.post(
            f"{self.base_url}/ocr",
            json={"image_base64": image_base64},
        )
        response.raise_for_status()
        data = response.json()
        content = (data.get("markdown") or "").strip()
        content = _clean_markdown(content)

        personal_info = _parse_personal_info(content)
        if not personal_info:
            personal_info = _parse_personal_info_cn(content)
        indicators = _parse_markdown_table(content)

        return {"personal_info": personal_info, "indicators": indicators, "raw_text": content}

    def extract_from_images(self, images_base64: list[str]) -> dict:
        all_indicators = []
        personal_info = {}
        raw_texts = []
        failed_pages = []
        for i, img in enumerate(images_base64):
            # 2026-08-26: 单页失败跳过, 不中断整份报告
            # (PaddleOCR-VL 对个别扫描页会崩且服务不自动恢复, 保底至少拿到能识别的页)
            try:
                result = self.extract_from_image(img)
            except Exception:
                failed_pages.append(i + 1)
                continue
            if result.get("personal_info"):
                for k, v in result["personal_info"].items():
                    if v is not None:
                        personal_info[k] = v
            if result.get("indicators"):
                all_indicators.extend(result["indicators"])
            if result.get("raw_text"):
                raw_texts.append(result["raw_text"])
        if failed_pages:
            logger.warning("VLM extract_from_images failed pages: %s", failed_pages)
        return {"personal_info": personal_info, "indicators": all_indicators,
                "raw_text": "\n\n".join(raw_texts)}

    def extract_conclusion(self, image_base64: str) -> str:
        """调用 PaddleOCR-VL 提取报告最后的结论/建议段落。"""
        CONCLUSION_PROMPT = """请提取这份体检报告最后的"总检建议与结论"或"医师建议""健康指导"等结论性段落的内容。

只输出该段落原文，不要表格、不要指标列表。如果没有找到，输出"NONE"。"""
        response = self.client.post(
            f"{self.base_url}/ocr",
            json={"image_base64": image_base64, "prompt": CONCLUSION_PROMPT},
        )
        response.raise_for_status()
        data = response.json()
        return (data.get("markdown") or "").strip()

    def extract_conclusion_from_images(self, images_base64: list[str]) -> Optional[str]:
        """从多页图像提取结论，取最后一页/最后几页的结果。"""
        # 结论通常在报告最后几页
        candidate_pages = images_base64[-3:] if len(images_base64) > 3 else images_base64
        texts = []
        for img in candidate_pages:
            try:
                t = self.extract_conclusion(img)
                if t and t.upper() != "NONE" and len(t) > 10:
                    # 2026-08-29: 结论输出可能混入 HTML 表格/图片标签, 剥除(仅结论路径)
                    t = re.sub(r"<table.*?</table>", "", t, flags=re.DOTALL)
                    t = re.sub(r"<[^>]+>", "", t)
                    t = re.sub(r"\n{2,}", "\n", t)
                    texts.append(t)
            except Exception:
                pass
        return "\n\n".join(texts) if texts else None


vlm_client = VLMClient()
