"""院级 hybrid OCR 开关: 文本层已含结论/检验表的医院可关闭 hybrid 补充 OCR。"""
from unittest.mock import patch


def test_pre_scan_hybrid_decision_disabled_for_kunming():
    from app.modules.report import service

    with patch.object(service, "_pdf_has_text", return_value=True), \
         patch.object(service, "_extract_pdf_text",
                      return_value="昆明医科大学第一附属医院\n体检报告") as extract:
        use_hybrid, pre_text = service._pre_scan_hybrid_decision("/tmp/x.pdf")

    assert use_hybrid is False
    assert "昆明" in pre_text
    extract.assert_called_once()
    assert extract.call_args.kwargs.get("hybrid") is False


def test_pre_scan_hybrid_decision_default_true():
    from app.modules.report import service

    with patch.object(service, "_pdf_has_text", return_value=True), \
         patch.object(service, "_extract_pdf_text", return_value="某医院\n体检报告"):
        use_hybrid, _ = service._pre_scan_hybrid_decision("/tmp/x.pdf")

    assert use_hybrid is True


def test_pre_scan_hybrid_decision_force_ocr_skips_pre_scan():
    from app.modules.report import service

    with patch.object(service, "_pdf_has_text", return_value=True), \
         patch.object(service, "_extract_pdf_text") as extract:
        use_hybrid, pre_text = service._pre_scan_hybrid_decision(
            "/tmp/x.pdf", force_ocr=True)

    assert use_hybrid is True
    assert pre_text == ""
    assert not extract.called


def test_imaging_report_title_combination_and_spaces():
    from app.modules.report.service import _is_imaging_report_title

    assert _is_imaging_report_title("CT 检查报告单")
    assert _is_imaging_report_title("心 电 图 报 告")
    assert _is_imaging_report_title("彩 色 多 普 勒 超 声 检 查 报 告 单")
    assert _is_imaging_report_title("医学影像诊断报告单")
    assert _is_imaging_report_title("温州医科大学附属第一医院CT检查报告单")

    # 单独“报告/影像”或句子式文本不得命中
    assert not _is_imaging_report_title("报告时间：2026-05-15")
    assert not _is_imaging_report_title("B超报告上出现肝大，需结合肝功能分析")
    assert not _is_imaging_report_title("影像、检验结果流程：门诊医生工作站→查询")
    assert not _is_imaging_report_title("体检报告单")
    assert not _is_imaging_report_title("见纸质报告")
    assert not _is_imaging_report_title("请结合超声报告")
    assert not _is_imaging_report_title("详见超声报告")
    assert _is_imaging_report_title(
        "申请单位：新疆生产建设兵团第十三师红星医院（医共体）心电诊断中心 心电图报告")


def _make_hybrid_pdf(path):
    import io

    import fitz
    from PIL import Image

    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 72), "检验项目 检查结果 单位 参考值 白细胞 5.0")
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, "PNG")
    for _ in range(2):
        page = doc.new_page()
        page.insert_image(fitz.Rect(72, 72, 200, 200), stream=buf.getvalue())
    doc.save(path)
    doc.close()


def test_hybrid_ocr_stops_after_imaging_report(tmp_path):
    from app.modules.report import service

    pdf = tmp_path / "hybrid.pdf"
    _make_hybrid_pdf(str(pdf))
    calls = []

    def fake_ocr(_img):
        calls.append(1)
        return {"raw_text": "超声检查报告单" if len(calls) == 1 else "CT检查报告单"}

    with patch.object(service.vlm_client, "extract_from_image", side_effect=fake_ocr):
        service._extract_pdf_text(str(pdf), hybrid=True)

    assert len(calls) == 1, "命中影像报告标题后应停止后续 OCR"


def test_hybrid_ocr_keep_after_imaging_override(tmp_path):
    from app.modules.report import service

    pdf = tmp_path / "hybrid.pdf"
    _make_hybrid_pdf(str(pdf))
    calls = []

    def fake_ocr(_img):
        calls.append(1)
        return {"raw_text": "超声检查报告单"}

    with patch.object(service.vlm_client, "extract_from_image", side_effect=fake_ocr):
        service._extract_pdf_text(str(pdf), hybrid=True,
                                  keep_ocr_after_imaging=True)

    assert len(calls) == 2, "例外开关应保持全量 OCR"
