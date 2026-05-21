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
        raise NotImplementedError

    def extract_from_image(self, file_path: str) -> dict:
        raise NotImplementedError

    def _pdf_to_images(self, file_path: str) -> list:
        raise NotImplementedError

    def _process_page(self, image, page_index: int) -> dict:
        raise NotImplementedError

    def _extract_personal_info(self, image) -> dict:
        raise NotImplementedError

    def _flatten_text(self, ocr_result) -> list:
        raise NotImplementedError

    def _extract_table_indicators(self, ocr_result, image) -> list:
        raise NotImplementedError

    def _group_text_lines(self, ocr_result) -> list:
        raise NotImplementedError

    def _find_header_row(self, rows: list) -> list:
        raise NotImplementedError

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
