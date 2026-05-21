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
