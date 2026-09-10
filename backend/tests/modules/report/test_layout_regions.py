"""版面层 Phase 0 骨架护栏(2026-09-07): 视觉行提取的质量与稳定性。

验证口径(用户 2026-09-07 确认): 自动对照为主 —— 覆盖仓库既有全部样本 PDF
(结论回归样本 + 指标护栏样本 + 各地汇总新格式), 断言:
  1) 每份都能提取出视觉行、行结构合法;
  2) 坐标行不丢文本(按页字符覆盖率 ≥ 0.97 —— 行拼接 vs 页原始文本, 均去空白);
  3) 阅读序重排输出与 _extract_pdf_text(visual_sort=True) 兼容(字符级不丢)。

新格式接入时的纪律: 新增样本进"体检报告样例/"后本测试自动纳入(glob), 无需改断言。
"""
import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from app.modules.report.layout import coverage_ratio, extract_visual_rows, visual_text  # noqa: E402
from app.modules.report.service import _extract_pdf_text  # noqa: E402

SAMPLES_ROOT = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例"


def _all_samples():
    return sorted(
        glob.glob(os.path.join(SAMPLES_ROOT, "**", "*.pdf"), recursive=True)
        + glob.glob(os.path.join(SAMPLES_ROOT, "*.pdf"))
        + glob.glob(os.path.join(SAMPLES_ROOT, "*.PDF"), recursive=True)
    )


SAMPLES = _all_samples()


def test_samples_collected():
    assert len(SAMPLES) >= 20, f"样本应 ≥20 份, 实际 {len(SAMPLES)}"


def _rows_or_skip(pdf):
    """图片型 PDF(无文本层, 如钦州市中医医院)与既有护栏一致 skip。"""
    rows = extract_visual_rows(pdf)
    if not rows:
        import fitz
        doc = fitz.open(pdf)
        raw_len = sum(len(p.get_text().strip()) for p in doc)
        doc.close()
        if raw_len == 0:
            pytest.skip(f"{os.path.basename(pdf)}: 图片型 PDF 无文本层")
    return rows


@pytest.mark.parametrize("pdf", SAMPLES)
def test_rows_positive_and_valid(pdf):
    rows = _rows_or_skip(pdf)
    assert rows, f"{os.path.basename(pdf)}: 无视觉行"
    for r in rows:
        assert r.text.strip(), f"{os.path.basename(pdf)}: 空行"
        assert r.y1 >= r.y0 and r.x1 >= r.x0, f"{os.path.basename(pdf)}: bbox 非法"
        assert 1 <= r.page, f"{os.path.basename(pdf)}: page 非法"


@pytest.mark.parametrize("pdf", SAMPLES)
def test_no_text_loss(pdf):
    rows = _rows_or_skip(pdf)
    ratio = coverage_ratio(pdf, rows)
    assert ratio >= 0.97, (
        f"{os.path.basename(pdf)}: 视觉行字符覆盖率 {ratio:.3f} < 0.97 (丢文本?)")


@pytest.mark.parametrize("pdf", SAMPLES)
def test_visual_text_has_no_more_chars_than_raw(pdf):
    # 阅读序重排是"重排"不是"改写": 字符总量应 ≤ 原始文本总量(+页标记)
    rows = extract_visual_rows(pdf)
    vt = visual_text(rows, page_mark=False)
    raw = _extract_pdf_text(pdf).replace("--- Page", "")
    assert len(vt.replace(" ", "")) <= len(raw.replace(" ", "")) * 1.01, (
        f"{os.path.basename(pdf)}: 重排输出字符异常膨胀")


# === Phase 1 自动对照(2026-09-07): 提取层带标志行(DB signal_flag>0)必须落在表区内 ===
# 金标 = report_indicator.signal_flag > 0 且 raw_text IS NULL 的行(提取层判黄产物);
# 断言每个 (名称, 结果) 存在于同一"逻辑行"(同 y 聚簇)内 —— 即表行含名称列与结果列。
_H004_PDFS = {
    21: "体检报告样例/各地汇总/德宏州人民_H003_10.pdf".replace("德宏州人民_H003_10", "") + "",  # placeholder
}
import pymysql

# (db, report_id) → pdf: H004 7 家 + H003 5 家(均为新代码端到端验收落库报告)
_REGION_CASES = {
    ("H004", 21): ("hospital_H004", "各地汇总/厦门华西_H004_11.pdf"),
    ("H004", 22): ("hospital_H004", "各地汇总/厦门弘爱_H004_11.pdf"),
    ("H004", 23): ("hospital_H004", "各地汇总/山东省立_H004_11.pdf"),
    ("H004", 24): ("hospital_H004", "各地汇总/日照人民_H004_11.pdf"),
    ("H004", 25): ("hospital_H004", "各地汇总/茂名人民_H004_11.pdf"),
    ("H004", 26): ("hospital_H004", "各地汇总/莆田九十五_H004_11.pdf"),
    ("H004", 27): ("hospital_H004", "各地汇总/齐鲁青岛_H004_11.pdf"),
    ("H003", 20): ("hospital_H003", "各地汇总/德宏州人民_H003_10.pdf"),
    ("H003", 22): ("hospital_H003", "各地汇总/滨州人民_H003_10.PDF"),
    ("H003", 23): ("hospital_H003", "各地汇总/潮州第一_H003_10.pdf"),
    ("H003", 24): ("hospital_H003", "各地汇总/福建第二_H003_10.PDF"),
    ("H003", 25): ("hospital_H003", "各地汇总/马鞍山人民_H003_10.pdf"),
}


def _flagged_rows(db: str, report_id: int):
    conn = pymysql.connect(host="127.0.0.1", user="root", password="root", db=db)
    cur = conn.cursor()
    cur.execute(
        "SELECT item_name, result_value FROM report_indicator "
        "WHERE report_id=%s AND raw_text IS NULL AND signal_flag > 0 "
        "AND result_value IS NOT NULL", (report_id,))
    rows = [(n.strip(), str(r)) for n, r in cur.fetchall() if n and r]
    conn.close()
    return rows


def _normalize(s: str) -> str:
    return s.replace(" ", "").replace("\u3000", "")


def _logical_cell_lines(page_rows):
    """同 y(≤1.0)聚簇 → 逻辑行(cells 文本, 按 x 序)。"""
    lines = []
    for r in sorted(page_rows, key=lambda r: (r.y0, r.x0)):
        if lines and abs(r.y0 - lines[-1][0]) <= 1.0:
            lines[-1][1].append(r)
        else:
            lines.append((r.y0, [r]))
    return [[c.text.strip() for c in cells] for _, cells in lines]


@pytest.mark.parametrize("key", sorted(_REGION_CASES))
def test_flagged_rows_inside_table_region(key):
    db, rel = _REGION_CASES[key]
    report_id = key[1]
    pdf = os.path.join(SAMPLES_ROOT, rel)
    if not os.path.exists(pdf):
        pytest.skip(f"样本缺失: {pdf}")
    flagged = _flagged_rows(db, report_id)
    if not flagged:
        pytest.skip(f"report {report_id} 无提取层带标志行")
    rows = _rows_or_skip(pdf)
    # 页 → 逻辑行
    by_page = {}
    for r in rows:
        by_page.setdefault(r.page, []).append(r)
    # Phase 1 口径: region 召回(名称可折行/跨 y) —— 名称去空格须出现在某 region 文本,
    # 且同一 region 内存在以 result 开头的单元格(值列)。列语义严格校验在 Phase 2 接入后。
    from app.modules.report.layout import detect_table_regions

    regions = detect_table_regions(pdf)
    reg_texts = []
    reg_cells = []
    for reg in regions:
        by_line = {}
        for r in reg.rows:
            by_line.setdefault(round(r.y0, 1), []).append(r)
        lines = []
        for y in sorted(by_line):
            lines.append("".join(c.text for c in sorted(by_line[y], key=lambda c: c.x0)))
        reg_texts.append("".join(_normalize(x) for x in lines))
        reg_cells.append([_normalize(c.text) for c in reg.rows])
    import re as _re

    def _name_variants(tn):
        # 折行名("估计肾小球滤过率（eGFR)" / "EB 病毒衣壳抗原\nIgG(IgG/VCA)")
        # 可能被值行隔开 → 允许剥尾(括号组/拉丁缩写)取前缀, 迭代至多 3 次
        out = [tn]
        cur = tn
        for _ in range(3):
            m = _re.search(
                r"(?:[（(][^（()）]{1,24}[)）]|[A-Za-z0-9/·（）()]{4,})$", cur)
            if not m:
                break
            cur = cur[:m.start()]
            out.append(cur)
        return out

    failures = []
    for name, result in flagged:
        n_ok = _normalize(name)
        tok = result.strip().split()[0] if result.strip() else result
        hit = False
        for rt, rc in zip(reg_texts, reg_cells):
            if any(v in rt for v in _name_variants(n_ok)) \
                    and any(c.startswith(result) or c.startswith(tok) for c in rc):
                hit = True
                break
        if not hit:
            failures.append(f"{name}|{result}")
    assert not failures, (
        f"report {report_id}: 以下提取层带标志行不在任何表区内(漏区/或 signals 垃圾): {failures}")
