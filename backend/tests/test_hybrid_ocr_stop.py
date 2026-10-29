"""通用 hybrid OCR 早停: OCR 到影像报告页(含"报告")后跳过后续 OCR。"""
from unittest.mock import patch

import fitz


def _make_pdf(path, n=3):
    doc = fitz.open()
    for _ in range(n):
        page = doc.new_page()
        page.insert_text((72, 72), "x")  # 文本 <10 字 → 触发 hybrid OCR
    doc.save(str(path))
    doc.close()


def test_imaging_report_page_stops_subsequent_ocr(tmp_path):
    from app.modules.report import service

    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf)
    calls = []

    def fake_ocr(img):
        calls.append(1)
        if len(calls) == 1:
            return {"raw_text": "超声检查图文报告单\n超声诊断：脂肪肝"}
        return {"raw_text": "检验项目\n白细胞 6.0"}

    with patch.object(service.vlm_client, "extract_from_image", side_effect=fake_ocr):
        text = service._extract_pdf_text(str(pdf), hybrid=True)

    assert len(calls) == 1
    assert "超声检查图文报告单" in text


def test_lab_table_page_does_not_stop_ocr(tmp_path):
    from app.modules.report import service

    pdf = tmp_path / "b.pdf"
    _make_pdf(pdf)
    calls = []

    def fake_ocr(img):
        calls.append(1)
        return {"raw_text": "检验项目\n白细胞 6.0\n参考范围 3.5-9.5"}

    with patch.object(service.vlm_client, "extract_from_image", side_effect=fake_ocr):
        service._extract_pdf_text(str(pdf), hybrid=True)

    assert len(calls) == 3


def test_force_ocr_does_not_early_stop(tmp_path):
    from app.modules.report import service

    pdf = tmp_path / "c.pdf"
    _make_pdf(pdf)
    calls = []

    def fake_ocr(img):
        calls.append(1)
        return {"raw_text": "CT检查报告\nCT诊断：未见异常"}

    with patch.object(service.vlm_client, "extract_from_image", side_effect=fake_ocr):
        service._extract_pdf_text(str(pdf), hybrid=True, force_ocr=True)

    assert len(calls) == 3
