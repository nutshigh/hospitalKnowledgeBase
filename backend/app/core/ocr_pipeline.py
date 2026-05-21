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

    def _match_header_columns(self, headers: list) -> dict:
        raise NotImplementedError

    def _row_to_indicator(self, row_cells: list, col_mapping: dict) -> dict:
        raise NotImplementedError

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
