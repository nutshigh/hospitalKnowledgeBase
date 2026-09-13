"""总检建议/结论展示链路回归(确定性层, 无 LLM)。

覆盖 app.modules.report.service 的 _locate_findings_sections + _reflow_conclusion_lines
+ _parse_numbered_titles(结论段落定位/重排/确定性条目标题解析)。
运行: pytest tests/modules/report/test_conclusion_regression.py
"""
import re

import pytest
import fitz

from app.modules.report.report_profiles import compile_profile, match_profile
from app.modules.report.service import (
    _locate_findings_sections,
    _parse_numbered_titles,
    _parse_summary_item_titles,
    _reflow_conclusion_lines,
)
from tests.modules.report.conclusion_samples import SAMPLES, SAMPLES_ROOT

TEXT_SAMPLES = [s for s in SAMPLES if not s.get("ocr")]
OCR_SAMPLES = [s for s in SAMPLES if s.get("ocr")]
# 仅对配置了对应期望字段的样本参数化, 避免"运行即跳过"占位
TITLE_SAMPLES = [s for s in TEXT_SAMPLES if s.get("expected_titles")]
SUMMARY_TITLE_SAMPLES = [s for s in TEXT_SAMPLES if s.get("expected_summary_titles")]
INDICATOR_SAMPLES = [s for s in TEXT_SAMPLES if s.get("indicator_expected")]
# 冒烟级样本(弱断言): 能定位、够长、无脏行 —— 防"规则崩坏", 不做逐字验收
SMOKE_SAMPLES = [s for s in TEXT_SAMPLES if s.get("smoke")]
FULL_SAMPLES = [s for s in TEXT_SAMPLES if not s.get("smoke")]


def _extract_sample_text(sample: dict) -> str:
    """生产同款文本: 每页 --- Page N --- 标记; visual 模板按坐标排序。"""
    doc = fitz.open(str(SAMPLES_ROOT / sample["rel"]))
    parts = []
    for p in range(doc.page_count):
        if sample.get("visual"):
            blocks = doc[p].get_text("blocks")
            blocks.sort(key=lambda b: (round(b[1] / 10), b[0]))
            t = "\n".join(b[4].strip() for b in blocks if b[4].strip())
        else:
            t = doc[p].get_text().strip()
        if t:
            parts.append(f"--- Page {p + 1} ---\n{t}")
    doc.close()
    return "\n\n".join(parts)


def _polish(sample: dict) -> str:
    """按生产管线提取文本(含医院档案)后走切段+重排(与 _extract_conclusion_async 同构:
    anchor_only / table_conclusion 一并支持)。"""
    full = _extract_sample_text(sample)
    prof = compile_profile(match_profile(full))
    section = _locate_findings_sections(
        full, extra_break_re=prof["extra_break_re"], extra_skip_re=prof["extra_skip_re"],
        extra_anchor_re=prof["extra_anchor_re"], anchor_only=bool(prof.get("anchor_only")))
    assert section, f"[{sample['key']}] 结论段落定位失败"
    if prof.get("table_conclusion"):
        from app.modules.report.service import _reflow_table_conclusion
        return _reflow_table_conclusion(section)
    return _reflow_conclusion_lines(section)


# 冒烟级通用脏词(页眉/分检/签名) —— smoke 样本不做逐份 forbidden 定制
_SMOKE_DIRTY = ["体检编号", "流水号", "姓名：", "性别：", "体 格 检 查", "检 验 报 告",
                "检验报告", "初检", "终审医生", "咨询电话", "欢迎您", "温馨提示"]


@pytest.mark.parametrize("sample", FULL_SAMPLES, ids=[s["key"] for s in FULL_SAMPLES])
def test_polish_clean_and_complete(sample):
    text = _polish(sample)
    lines = [ln.strip() for ln in text.splitlines()]
    assert text, f"[{sample['key']}] 重排输出为空"
    for bad in sample["forbidden"]:
        for ln in lines:
            assert bad not in ln, (
                f"[{sample['key']}] 出现脏行: {bad!r} 于行: {ln[:60]!r}"
            )
    for frag in sample["must_contain"]:
        assert frag in text, f"[{sample['key']}] 缺少关键内容: {frag!r}"


@pytest.mark.parametrize("sample", SMOKE_SAMPLES, ids=[s["key"] for s in SMOKE_SAMPLES])
def test_smoke_polish(sample):
    """冒烟级(新验收/未逐字精修样本): 能定位、够长、无通用脏行。"""
    text = _polish(sample)
    assert len(text) >= sample.get("min_len", 150), (
        f"[{sample['key']}] 结论段过短: {len(text)}字"
    )
    for ln in text.splitlines():
        for bad in _SMOKE_DIRTY:
            assert bad not in ln, f"[{sample['key']}] 冒烟脏行: {bad!r} 于 {ln[:50]!r}"


@pytest.mark.parametrize("sample", TITLE_SAMPLES, ids=[s["key"] for s in TITLE_SAMPLES])
def test_deterministic_titles(sample):
    text = _polish(sample)
    titles = set(_parse_numbered_titles(text))
    for t in sample["expected_titles"]:
        assert t in titles, f"[{sample['key']}] 确定性标题解析缺: {t!r}"
    # 2026-09-10: 兜底标题不得多产(方法名半截/科普句; 用户 09-10 报告的问题)
    for t in sample.get("forbidden_titles", []):
        assert t not in titles, (
            f"[{sample['key']}] 兜底标题不应有 {t!r}; 实际 {sorted(titles)}")


@pytest.mark.parametrize("sample", SUMMARY_TITLE_SAMPLES, ids=[s["key"] for s in SUMMARY_TITLE_SAMPLES])
def test_summary_item_titles(sample):
    """小结标题(序号+【】)下分行条目的确定性解析(提取对账 A 步)。"""
    text = _polish(sample)
    titles = set(_parse_summary_item_titles(text))
    for t in sample["expected_summary_titles"]:
        assert t in titles, f"[{sample['key']}] 小结内容行解析缺: {t!r}"


def _norm_ind(s: str) -> str:
    return re.sub(r"[\s（）()·,，、＊*]", "", s)


def _extract_indicator_names(text: str) -> list:
    from app.modules.report.table_extractor import (
        extract_column_table_rows,
        extract_indicator_rows,
    )
    return [_norm_ind(r["item_name"]) for r in (extract_indicator_rows(text) + extract_column_table_rows(text))]


@pytest.mark.parametrize("sample", INDICATOR_SAMPLES, ids=[s["key"] for s in INDICATOR_SAMPLES])
def test_indicator_layer(sample):
    """指标层回归(需求①): 与生产同序 —— 先挖除结论段再行式/列式提取指标。

    断言: ①已验收的黄/红指标(DB 固化)都能被提出(切段规则若误挖化验表立即失败);
    ②挖除结论段导致的指标行损耗有上限(切段范围若失控吃掉化验表会超限)。
    """
    text = _extract_sample_text(sample)
    names_before = _extract_indicator_names(text)
    section = _locate_findings_sections(text)
    assert section, f"[{sample['key']}] 结论段落定位失败(指标层前置)"
    if len(section) > 50:
        text = text.replace(section, "")
    names_after = _extract_indicator_names(text)
    loss = len(names_before) - len(names_after)
    assert loss <= 15, (
        f"[{sample['key']}] 挖除结论段吃掉指标行过多: before={len(names_before)} "
        f"after={len(names_after)} loss={loss}(结论段定位范围失控?)"
    )
    for expect in sample["indicator_expected"]:
        g = _norm_ind(expect)
        assert any(g == x or (len(g) >= 3 and (g in x or x in g)) for x in names_after), (
            f"[{sample['key']}] 指标提取缺(黄/红固化): {expect!r}"
        )


def _ocr_service_up(url: str = "http://localhost:8006") -> bool:
    try:
        import httpx
        return httpx.get(f"{url}/health", timeout=2).status_code == 200
    except Exception:
        return False


@pytest.mark.parametrize("sample", OCR_SAMPLES, ids=[s["key"] for s in OCR_SAMPLES])
def test_ocr_sample(sample):
    """图片型 PDF(无文本层): 逐页 OCR → 锚点切段 → 重排(与钦州中重跑同款)。

    需要本地 PaddleOCR-VL 服务(8006)在线; 不在线则跳过。
    """
    if not _ocr_service_up():
        pytest.skip("OCR 服务(8006)不在线")
    import base64 as _b64
    from app.core.vlm_client import VLMClient
    vlm = VLMClient("http://localhost:8006")
    doc = fitz.open(str(SAMPLES_ROOT / sample["rel"]))
    texts = []
    for p in range(doc.page_count):
        pix = doc[p].get_pixmap(matrix=fitz.Matrix(2.2, 2.2))
        img = _b64.b64encode(pix.tobytes("png")).decode()
        try:
            r = vlm.extract_from_image(img)
        except Exception:
            continue
        raw = (r.get("raw_text") or "").strip()
        if raw:
            texts.append(raw)
    doc.close()
    assert texts, f"[{sample['key']}] OCR 全部失败"
    full = "\n\n".join(texts)
    polished = _reflow_conclusion_lines(_locate_findings_sections(full) or "")
    assert polished, f"[{sample['key']}] OCR 文本切段失败"
    lines = [ln.strip() for ln in polished.splitlines()]
    for bad in sample["forbidden"]:
        for ln in lines:
            assert bad not in ln, f"[{sample['key']}] OCR 结论含脏行: {bad!r}"
    for frag in sample["must_contain"]:
        assert frag in polished, f"[{sample['key']}] OCR 结论缺: {frag!r}"


def test_profile_visual_flag_consistent_with_samples():
    """样本里的 visual 标记必须与医院档案(report_profiles)一致, 防两处不同步。"""
    for sample in TEXT_SAMPLES:
        doc = fitz.open(str(SAMPLES_ROOT / sample["rel"]))
        t = "\n".join(p.get_text() for p in doc)
        doc.close()
        prof = match_profile(t)
        assert bool(prof.get("visual_sort")) == bool(sample.get("visual")), (
            f"[{sample['key']}] 样本 visual={sample.get('visual')} "
            f"但档案匹配 visual_sort={prof.get('visual_sort')}"
        )
