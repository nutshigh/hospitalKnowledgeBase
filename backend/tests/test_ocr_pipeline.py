import pytest
from app.core.ocr_pipeline import OcrPipeline


@pytest.fixture
def pipeline():
    return OcrPipeline()


class TestParseRefRange:
    def test_standard_range(self, pipeline):
        assert pipeline._parse_ref_range("3.5-9.5") == ("3.5", "9.5")

    def test_range_with_spaces(self, pipeline):
        assert pipeline._parse_ref_range("3.5 - 9.5") == ("3.5", "9.5")

    def test_range_with_tilde(self, pipeline):
        assert pipeline._parse_ref_range("3.5~9.5") == ("3.5", "9.5")

    def test_chinese_separator(self, pipeline):
        assert pipeline._parse_ref_range("3.5到9.5") == ("3.5", "9.5")
        assert pipeline._parse_ref_range("3.5至9.5") == ("3.5", "9.5")

    def test_less_than(self, pipeline):
        assert pipeline._parse_ref_range("<5.0") == (None, "5.0")
        assert pipeline._parse_ref_range("＜5.0") == (None, "5.0")

    def test_greater_than(self, pipeline):
        assert pipeline._parse_ref_range(">100") == ("100", None)
        assert pipeline._parse_ref_range("＞100") == ("100", None)

    def test_non_range_text(self, pipeline):
        assert pipeline._parse_ref_range("正常") == (None, None)
        assert pipeline._parse_ref_range("阴性") == (None, None)

    def test_decimal_range(self, pipeline):
        assert pipeline._parse_ref_range("0.00-0.50") == ("0.00", "0.50")


class TestMatchHeaderColumns:
    @pytest.fixture
    def pipeline(self):
        return OcrPipeline()

    def test_exact_match(self, pipeline):
        headers = [
            {"text": "检验项目", "bbox": [[0,0],[50,0],[50,20],[0,20]], "confidence": 0.99, "cx": 25, "cy": 10},
            {"text": "结果", "bbox": [[50,0],[80,0],[80,20],[50,20]], "confidence": 0.99, "cx": 65, "cy": 10},
            {"text": "单位", "bbox": [[80,0],[100,0],[100,20],[80,20]], "confidence": 0.99, "cx": 90, "cy": 10},
            {"text": "参考范围", "bbox": [[100,0],[150,0],[150,20],[100,20]], "confidence": 0.99, "cx": 125, "cy": 10},
        ]
        mapping = pipeline._match_header_columns(headers)
        assert mapping[0] == "item_name"
        assert mapping[1] == "result"
        assert mapping[2] == "unit"
        assert mapping[3] == "ref_range"

    def test_partial_match(self, pipeline):
        headers = [
            {"text": "项目名称", "bbox": [[0,0],[50,0],[50,20],[0,20]], "confidence": 0.99, "cx": 25, "cy": 10},
            {"text": "测定值", "bbox": [[50,0],[80,0],[80,20],[50,20]], "confidence": 0.99, "cx": 65, "cy": 10},
            {"text": "英文缩写", "bbox": [[80,0],[110,0],[110,20],[80,20]], "confidence": 0.99, "cx": 95, "cy": 10},
        ]
        mapping = pipeline._match_header_columns(headers)
        assert mapping[0] == "item_name"
        assert mapping[1] == "result"
        assert mapping[2] == "item_code"

    def test_unknown_column(self, pipeline):
        headers = [
            {"text": "检验项目", "bbox": [[0,0],[50,0],[50,20],[0,20]], "confidence": 0.99, "cx": 25, "cy": 10},
            {"text": "XYZ奇怪列", "bbox": [[50,0],[100,0],[100,20],[50,20]], "confidence": 0.99, "cx": 75, "cy": 10},
        ]
        mapping = pipeline._match_header_columns(headers)
        assert mapping[0] == "item_name"
        assert mapping[1] == "unknown"

    def test_empty_headers(self, pipeline):
        mapping = pipeline._match_header_columns([])
        assert mapping == {}


class TestRowToIndicator:
    @pytest.fixture
    def pipeline(self):
        return OcrPipeline()

    def test_basic_row(self, pipeline):
        row = [
            {"text": "白细胞", "confidence": 0.95, "cx": 25, "cy": 50},
            {"text": "5.2", "confidence": 0.98, "cx": 65, "cy": 50},
            {"text": "10^9/L", "confidence": 0.97, "cx": 90, "cy": 50},
            {"text": "3.5-9.5", "confidence": 0.96, "cx": 125, "cy": 50},
        ]
        col_mapping = {0: "item_name", 1: "result", 2: "unit", 3: "ref_range"}
        result = pipeline._row_to_indicator(row, col_mapping)
        assert result["item_name"] == "白细胞"
        assert result["result_value"] == "5.2"
        assert result["unit"] == "10^9/L"
        assert result["ref_range_low"] == "3.5"
        assert result["ref_range_high"] == "9.5"

    def test_unknown_column_inference(self, pipeline):
        row = [
            {"text": "总蛋白", "confidence": 0.95, "cx": 25, "cy": 50},
            {"text": "72.5", "confidence": 0.98, "cx": 65, "cy": 50},
            {"text": "60.0-80.0", "confidence": 0.96, "cx": 90, "cy": 50},
        ]
        col_mapping = {0: "item_name", 1: "unknown", 2: "unknown"}
        result = pipeline._row_to_indicator(row, col_mapping)
        assert result["item_name"] == "总蛋白"
        assert result["result_value"] == "72.5"
        assert result["ref_range_low"] == "60.0"
        assert result["ref_range_high"] == "80.0"

    def test_skip_row_without_item_name(self, pipeline):
        row = [
            {"text": "", "confidence": 0.5, "cx": 25, "cy": 50},
        ]
        col_mapping = {0: "item_name"}
        result = pipeline._row_to_indicator(row, col_mapping)
        assert result == {}


class TestGroupTextLines:
    @pytest.fixture
    def pipeline(self):
        return OcrPipeline()

    def test_two_rows(self, pipeline):
        ocr_result = [
            [
                [[[0, 0], [50, 0], [50, 18], [0, 18]], ["项目", 0.99]],
                [[[60, 0], [90, 0], [90, 18], [60, 18]], ["结果", 0.99]],
                [[[0, 32], [50, 32], [50, 50], [0, 50]], ["白细胞", 0.98]],
                [[[60, 32], [90, 32], [90, 50], [60, 50]], ["5.2", 0.97]],
            ]
        ]
        rows = pipeline._group_text_lines(ocr_result)
        assert len(rows) == 2
        assert rows[0][0]["text"] == "项目"
        assert rows[1][0]["text"] == "白细胞"

    def test_empty_result(self, pipeline):
        rows = pipeline._group_text_lines(None)
        assert rows == []


class TestFindHeaderRow:
    @pytest.fixture
    def pipeline(self):
        return OcrPipeline()

    def test_find_header_by_keyword_density(self, pipeline):
        rows = [
            [{"text": "医院名称", "cx": 100, "cy": 10, "bbox": [[0,0],[80,0],[80,18],[0,18]], "confidence": 0.99}],
            [{"text": "检验项目", "cx": 25, "cy": 30, "bbox": [[0,20],[50,20],[50,38],[0,38]], "confidence": 0.99},
             {"text": "结果", "cx": 75, "cy": 30, "bbox": [[60,20],[90,20],[90,38],[60,38]], "confidence": 0.99},
             {"text": "参考范围", "cx": 130, "cy": 30, "bbox": [[100,20],[155,20],[155,38],[100,38]], "confidence": 0.99}],
        ]
        header = pipeline._find_header_row(rows)
        assert len(header) == 3
        assert header[0]["text"] == "检验项目"

    def test_no_keywords_returns_empty(self, pipeline):
        rows = [
            [{"text": "报告日期", "cx": 100, "cy": 10, "bbox": [[0,0],[80,0],[80,18],[0,18]], "confidence": 0.99}],
            [{"text": "白细胞", "cx": 25, "cy": 30, "bbox": [[0,20],[50,20],[50,38],[0,38]], "confidence": 0.99}],
        ]
        header = pipeline._find_header_row(rows)
        assert len(header) == 0
