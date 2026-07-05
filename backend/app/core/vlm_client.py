import re
from httpx import Client, Timeout
from app.config import settings

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

    匹配常见格式：姓名:XXX / 姓名 XXX、性别:男、年龄:30岁、体检日期:2024-01-01
    """
    info = {}
    patterns = {
        "name": r"姓\s*名[:：\s]+([^\s,，\|]{2,10})",
        "gender": r"性\s*别[:：\s]+(男|女)",
        "age": r"年\s*龄[:：\s]*(\d+)",
        "check_date": r"(?:体检日期|检查日期|日期|日\s*期)[:：\s]+(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            val = m.group(1).strip()
            if val:
                info[key] = val
    return info


def _parse_markdown_table(text: str) -> list[dict]:
    """Parse a Markdown table into a list of indicator dicts."""
    lines = text.split("\n")
    table_rows = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if all(c.replace("-", "").replace(" ", "") == "" for c in cells):
                continue  # separator row
            table_rows.append(cells)

    if len(table_rows) < 2:
        return []

    # Map header columns
    header = table_rows[0]
    col_map = _match_header_columns(header)

    indicators = []
    seen = set()
    for row in table_rows[1:]:
        indicator = _row_to_indicator(row, col_map)
        name = indicator.get("item_name", "").strip()
        if not name or name in ("(each indicator)", "项目名称", "结果"):
            continue
        # Deduplicate: same name + same value → skip
        key = (name, indicator.get("result", ""))
        if key in seen:
            continue
        seen.add(key)
        # If ref_low/ref_high still contain a range, parse them
        for ref_key in ("ref_low", "ref_high"):
            val = indicator.get(ref_key)
            if val and re.search(r"[\d.]+\s*[-~—到至]\s*[\d.]+", str(val)):
                lo, hi = _parse_ref_range(str(val))
                indicator["ref_low"] = lo
                indicator["ref_high"] = hi
                break
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
            indicator["unit"] = cell
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
    return None, None


class VLMClient:
    def __init__(self, base_url: str = settings.OCR_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.model = settings.OCR_MODEL
        custom_prompt = getattr(settings, "OCR_PROMPT", None)
        self.prompt = custom_prompt.strip() if custom_prompt else OCR_PROMPT
        self.client = Client(timeout=Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0))

    def extract_from_image(self, image_base64: str) -> dict:
        """调用 PaddleOCR-VL 服务（/ocr），返回 {personal_info, indicators}。

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

        return {"personal_info": personal_info, "indicators": indicators}

    def extract_from_images(self, images_base64: list[str]) -> dict:
        all_indicators = []
        personal_info = {}
        for img in images_base64:
            result = self.extract_from_image(img)
            if result.get("personal_info"):
                for k, v in result["personal_info"].items():
                    if v is not None:
                        personal_info[k] = v
            if result.get("indicators"):
                all_indicators.extend(result["indicators"])
        return {"personal_info": personal_info, "indicators": all_indicators}


vlm_client = VLMClient()
