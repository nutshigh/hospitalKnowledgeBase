import re
import numpy as np
from typing import Optional


COLUMN_KEYWORDS = {
    "item_name": ["项目", "检验项目", "指标名称", "检查项目", "测定项目", "项目名称", "检查指标"],
    "item_code": ["缩写", "英文简称", "代码", "英文缩写", "代号", "缩写符号"],
    "result":    ["结果", "测定值", "检验结果", "实测值", "检测结果", "测定结果", "检查结果", "数值"],
    "unit":      ["单位", "计量单位", "测量单位"],
    "ref_range": ["参考区间", "参考范围", "正常范围", "参考值", "正常值", "参考", "标准范围"],
    "flag":      ["提示", "标志", "异常", "箭头", "↑↓", "判断"],
}


class OcrPipeline:
    def __init__(self, use_gpu: bool = False):
        self._use_gpu = use_gpu
        self._initialized = False
        self._ocr = None

    def _init(self):
        if self._initialized:
            return
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(
            use_angle_cls=True, lang="ch",
            use_gpu=self._use_gpu, show_log=False,
        )
        self._initialized = True

    def extract_from_pdf(self, file_path: str) -> dict:
        images = self._pdf_to_images(file_path)
        all_indicators = []
        personal_info = {}

        for i, img in enumerate(images):
            page_result = self._process_page(img, page_index=i)
            if i == 0 and page_result.get("personal_info"):
                personal_info = page_result["personal_info"]
            all_indicators.extend(page_result.get("indicators", []))

        return {"personal_info": personal_info, "indicators": all_indicators}

    def extract_from_image(self, file_path: str) -> dict:
        import cv2
        img = cv2.imread(file_path)
        return self._process_page(img, page_index=0)

    def _pdf_to_images(self, file_path: str) -> list:
        import fitz
        doc = fitz.open(file_path)
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:
                img = img[:, :, :3]
            images.append(img)
        doc.close()
        return images

    def _process_page(self, image, page_index: int) -> dict:
        self._init()
        result = self._ocr.ocr(image, cls=True)

        personal_info = {}
        if page_index == 0:
            personal_info = self._extract_personal_info(image)

        indicators = self._extract_table_indicators(result, image)
        return {"personal_info": personal_info, "indicators": indicators}

    def _extract_personal_info(self, image) -> dict:
        self._init()
        result = self._ocr.ocr(image, cls=True)
        all_text = self._flatten_text(result)

        info = {}
        patterns = {
            "name": r"姓名[:：\s]*(\S+)",
            "gender": r"性别[:：\s]*(男|女)",
            "age": r"年龄[:：\s]*(\d+)",
            "check_date": r"(?:检查|体检|报告)?日期[:：\s]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})",
        }
        full = "\n".join(all_text)
        for key, pat in patterns.items():
            m = re.search(pat, full)
            if m:
                info[key] = m.group(1)
        return info

    def _flatten_text(self, ocr_result) -> list:
        texts = []
        if ocr_result and ocr_result[0]:
            for line in ocr_result[0]:
                if len(line) >= 2:
                    texts.append(str(line[1][0]))
        return texts

    def _extract_table_indicators(self, ocr_result, image) -> list:
        lines = self._group_text_lines(ocr_result)
        if len(lines) < 2:
            return []

        header_idx = 0
        all_keywords = set()
        for aliases in COLUMN_KEYWORDS.values():
            all_keywords.update(aliases)

        best_score = 0
        for i, row in enumerate(lines[:min(5, len(lines))]):
            text = " ".join(cell["text"] for cell in row)
            score = sum(1 for kw in all_keywords if kw in text)
            if score > best_score:
                best_score = score
                header_idx = i

        if best_score == 0:
            return []

        headers = lines[header_idx]
        col_mapping = self._match_header_columns(headers)

        indicators = []
        for row in lines[header_idx + 1:]:
            indicator = self._row_to_indicator(row, col_mapping)
            if indicator and indicator.get("item_name"):
                indicators.append(indicator)
        return indicators

    def _group_text_lines(self, ocr_result) -> list[list[dict]]:
        if not ocr_result or not ocr_result[0]:
            return []

        items = []
        for line in ocr_result[0]:
            if len(line) >= 2:
                bbox = line[0]
                text = str(line[1][0])
                conf = float(line[1][1])
                items.append({
                    "text": text,
                    "bbox": bbox,
                    "confidence": conf,
                    "cx": (bbox[0][0] + bbox[2][0]) / 2,
                    "cy": (bbox[0][1] + bbox[2][1]) / 2,
                })

        items.sort(key=lambda x: x["cy"])
        if not items:
            return []

        avg_h = sum(abs(it["bbox"][2][1] - it["bbox"][0][1]) for it in items) / len(items)
        rows = []
        current_row = [items[0]]
        for it in items[1:]:
            if abs(it["cy"] - current_row[-1]["cy"]) < avg_h * 1.5:
                current_row.append(it)
            else:
                current_row.sort(key=lambda x: x["cx"])
                rows.append(current_row)
                current_row = [it]
        if current_row:
            current_row.sort(key=lambda x: x["cx"])
            rows.append(current_row)
        return rows

    def _find_header_row(self, rows: list) -> list[dict]:
        best_row, best_score = [], 0
        all_keywords = set()
        for aliases in COLUMN_KEYWORDS.values():
            all_keywords.update(aliases)

        for row in rows[:min(5, len(rows))]:
            text = " ".join(cell["text"] for cell in row)
            score = 0
            for kw in all_keywords:
                if kw in text:
                    score += 1
            if score > best_score:
                best_score = score
                best_row = row
        return best_row

    def _match_header_columns(self, headers: list[dict]) -> dict:
        mapping = {}

        def _jaccard(a: str, b: str) -> float:
            sa, sb = set(a), set(b)
            if not sa or not sb:
                return 0.0
            return len(sa & sb) / len(sa | sb)

        for i, cell in enumerate(headers):
            text = cell["text"].strip()
            best_key, best_score = None, 0.0
            for key, aliases in COLUMN_KEYWORDS.items():
                for alias in aliases:
                    score = _jaccard(text, alias)
                    if score > best_score:
                        best_score = score
                        best_key = key
            if best_score > 0.35:
                mapping[i] = best_key
            else:
                mapping[i] = "unknown"
        return mapping

    def _row_to_indicator(self, row_cells: list[dict], col_mapping: dict) -> dict:
        result = {}
        for i, cell in enumerate(row_cells):
            key = col_mapping.get(i, "unknown")
            text = cell["text"].strip()

            if key == "item_name":
                result["item_name"] = text
            elif key == "item_code":
                result["item_code"] = text
            elif key == "result":
                result["result_value"] = text
            elif key == "unit":
                result["unit"] = text
            elif key == "ref_range":
                low, high = self._parse_ref_range(text)
                result["ref_range_low"] = low
                result["ref_range_high"] = high
            elif key == "flag":
                pass
            else:
                if self._looks_like_number(text) and "result_value" not in result:
                    result["result_value"] = text
                elif self._looks_like_ref_range(text) and "ref_range_low" not in result:
                    low, high = self._parse_ref_range(text)
                    result["ref_range_low"] = low
                    result["ref_range_high"] = high

        result["confidence"] = min(
            c["confidence"] for c in row_cells
        ) if row_cells else 1.0

        return result if result.get("item_name") else {}

    def _parse_ref_range(self, text: str) -> tuple:
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

    def _looks_like_number(self, text: str) -> bool:
        return bool(re.match(r"^[\d.]+$", text))

    def _looks_like_ref_range(self, text: str) -> bool:
        return bool(re.match(r"^[<＞>\d].*[\d]|[～-].*[\d]", text))
