"""结论侧 conclusion_text 重提取护栏(2026-09-15)。

与 test_gx_bj_indicator_guard.py 同思路: 当前代码从 PDF **确定性**重提取结论段
(无 LLM 分支, 与生产 _extract_conclusion_async 的锚点链同构), 与 DB 验收快照
(baselines/h003|h004_baseline.json)的 conclusion_hash 比对 —— 在报告重跑前就能
发现结论切段/定位/reflow 的代码回归。

三组(显式名单, 防静默丢覆盖):
- MATCH(27): 严格 hash == 验收基线(2026-09-15 重跑+重生成基线后收口;
  2026-09-16 第三批 +3: 28 常逢龙/31 曹嘉冰/40 陈镜霓)。
- (无 PENDING 组: 原 6 份经重跑与基线更新后已转 MATCH。)
- HYBRID(9): 离线链与生产链条不等价(扫描/图片件取不到段; 或生产链有离线链没有的
  补偿: skip_lines 姓名页眉/生产跑早于现行签名截断等) —— 只锁离线链产物防漂移,
  不比对基线(生产 conclusion_text 由三件套 DB 快照守)。2026-09-16 第三批 +7。

2026-09-16: 第三批 10 份(样例 体检报告样例/0-第三批)纳入基线护栏:
- `_is_findings_anchor` 的"建议"类【】锚点只看括号内标题(旧实现"建议" in s 会把
  "【肺结节】建议您胸外科…"判成锚点, 其下一行又是锚点时单行段被空锚点规则整体
  丢弃 —— 常逢龙整条丢失; 修复后 28 由 DIFF 转 MATCH)。

2026-09-15 修复(均带纯函数断言): 钦州二重复行(_locate i==0)、茂名综述块漏剥
(块尾只认★)、冒号全角化(保留源字符)、单位行被英文残行规则跳过、签名后锚点
(潮州/德宏附录垃圾)、未闭合括号标题断行(齐鲁)、页眉行(28岁/医院名)、马鞍山
序号当结果、茂名插入页割断句归位。

不覆盖: 总检异常条目名单(LLM 生成, 非确定) —— 仍由三件套 DB 快照
(test_h003_baseline_guard.py / test_h004_baseline_guard.py)守。
"""
import hashlib
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.modules.report.service import (  # noqa: E402
    _compile_profile_re,
    _drop_review_block,
    _extract_pdf_text,
    _load_report_profiles,
    _locate_findings_sections,
    _reflow_conclusion_lines,
    _reflow_table_conclusion,
    _strip_ocr_html,
)

SAMPLES = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例"
GX = os.path.join(SAMPLES, "广西体检报告测试")
SUMM = os.path.join(SAMPLES, "各地汇总")
# 2026-09-16: 第三批 10 份(H003 27-31 + H004 38-41/43)样例在本仓库
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
B3 = os.path.join(REPO, "体检报告样例", "0-第三批")
BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines")

CASES = {
    ("h003", "1"): os.path.join(GX, "广西崇左市人民医院.pdf"),
    ("h003", "2"): os.path.join(GX, "广西壮族自治区人民医院.pdf"),
    ("h003", "3"): os.path.join(GX, "广西百色市右江民族医学院附属医院.pdf"),
    ("h003", "4"): os.path.join(GX, "广西贵港市东晖医院.pdf"),
    ("h003", "5"): os.path.join(GX, "广西防城港市中医医院.PDF"),
    ("h003", "6"): os.path.join(GX, "广西防城港市第一人民医院.pdf"),
    ("h003", "7"): os.path.join(SAMPLES, "陈美杉_H003_10.pdf"),
    ("h003", "20"): os.path.join(SUMM, "德宏州人民_H003_10.pdf"),
    ("h003", "22"): os.path.join(SUMM, "滨州人民_H003_10.PDF"),
    ("h003", "23"): os.path.join(SUMM, "潮州第一_H003_10.pdf"),
    ("h003", "24"): os.path.join(SUMM, "福建第二_H003_10.PDF"),
    ("h003", "25"): os.path.join(SUMM, "马鞍山人民_H003_10.pdf"),
    ("h003", "26"): os.path.join(SUMM, "池州人民_H003_10.pdf"),
    ("h003", "27"): os.path.join(B3, "东方医院", "鲍文祥103151.PDF"),
    ("h003", "28"): os.path.join(B3, "华山医院", "常逢龙08401X.pdf"),
    ("h003", "29"): os.path.join(B3, "六院金山", "包雁飞31061X.pdf"),
    ("h003", "30"): os.path.join(B3, "仁济医院", "陈磊27441X\u00a0.pdf"),
    ("h003", "31"): os.path.join(B3, "中医院", "曹嘉冰037053.pdf"),
    ("h004", "1"): os.path.join(GX, "广西柳州市人民医院.pdf"),
    ("h004", "2"): os.path.join(GX, "广西桂林市南溪山医院.pdf"),
    ("h004", "3"): os.path.join(GX, "广西梧州市中医医院.pdf"),
    ("h004", "29"): os.path.join(GX, "广西钦州市中医医院.pdf"),
    ("h004", "5"): os.path.join(GX, "广西钦州市第二人民医院.PDF"),
    ("h004", "6"): os.path.join(SAMPLES, "步新宇_H004_11.pdf"),
    ("h004", "21"): os.path.join(SUMM, "厦门华西_H004_11.pdf"),
    ("h004", "22"): os.path.join(SUMM, "厦门弘爱_H004_11.pdf"),
    ("h004", "23"): os.path.join(SUMM, "山东省立_H004_11.pdf"),
    ("h004", "24"): os.path.join(SUMM, "日照人民_H004_11.pdf"),
    ("h004", "25"): os.path.join(SUMM, "茂名人民_H004_11.pdf"),
    ("h004", "26"): os.path.join(SUMM, "莆田九十五_H004_11.pdf"),
    ("h004", "27"): os.path.join(SUMM, "齐鲁青岛_H004_11.pdf"),
    ("h004", "38"): os.path.join(B3, "安鹏063536.pdf"),
    ("h004", "39"): os.path.join(B3, "蔡超221833.PDF"),
    ("h004", "40"): os.path.join(B3, "陈镜霓220028.pdf"),
    ("h004", "41"): os.path.join(B3, "白玮衡31151X.pdf"),
    ("h004", "43"): os.path.join(B3, "高帅185516.pdf"),
}

# hash == 验收基线(2026-09-15 基线重生成后 24 份; 含原 PENDING 6 份与
# 原 HYBRID 中经修复后 hybrid 不再改变产物的德宏/潮州/柳州)
# 2026-09-16: +第三批可确定性重提取的 3 份(28 常逢龙/31 曹嘉冰/40 陈镜霓)
MATCH = {
    ("h003", "1"), ("h003", "2"), ("h003", "3"), ("h003", "4"), ("h003", "5"),
    ("h003", "6"), ("h003", "7"), ("h003", "20"), ("h003", "22"), ("h003", "23"),
    ("h003", "25"), ("h003", "26"), ("h003", "28"), ("h003", "31"),
    ("h004", "1"), ("h004", "2"), ("h004", "3"), ("h004", "5"), ("h004", "6"),
    ("h004", "21"), ("h004", "22"), ("h004", "23"), ("h004", "24"), ("h004", "25"),
    ("h004", "26"), ("h004", "27"), ("h004", "40"),
}

# 离线链(hybrid=False)与生产链条不等价: 扫描/图片件(离线取不到段), 或生产链
# 有离线链没有的补偿(skip_lines 姓名页眉 / 生产跑早于现行签名截断等)。
# **不比对基线**, 只锁定离线链产物防漂移; 其生产 conclusion_text 由三件套 DB
# 快照(test_h003/h004_baseline_guard.py)守。
# 2026-09-16: +第三批 7 份(27/29/30/38 纯扫描、39 文本层损坏走 force-ocr、
# 41 生产段尾"本次体检结果汇总"在签名后(离线签名截断不含)、43 离线多姓名页眉行)。
HYBRID = {
    ("h003", "24"): "da39a3ee5e6b4b0d",
    ("h004", "29"): "da39a3ee5e6b4b0d",
    ("h003", "27"): "da39a3ee5e6b4b0d",
    ("h003", "29"): "da39a3ee5e6b4b0d",
    ("h003", "30"): "da39a3ee5e6b4b0d",
    ("h004", "38"): "da39a3ee5e6b4b0d",
    ("h004", "39"): "da39a3ee5e6b4b0d",
    ("h004", "41"): "9a8ade6409ddbd31",
    ("h004", "43"): "f9509ddffdc3e70a",
}

BASELINE = {
    db: json.loads(
        (open(os.path.join(BASELINE_DIR, f"{db}_baseline.json"), encoding="utf-8")).read())
    for db in ("h003", "h004")
}

_PROFILES = None


def _re_extract_text(key):
    """与生产 _extract_conclusion_async 锚点分支同构(无 LLM 回退); 无锚点段返回 None。"""
    global _PROFILES
    pdf = CASES[key]
    if not os.path.exists(pdf):
        pytest.skip(f"{key} 样本缺失: {pdf}")
    if _PROFILES is None:
        _PROFILES = _load_report_profiles()
    match_profile, _ = _PROFILES
    text = _extract_pdf_text(pdf, hybrid=False)
    profile = match_profile(text)
    if profile.get("visual_sort"):
        # 与生产 process_task 一致: 多栏混排模板(防城港中/莆田)按视觉坐标重排后再切段
        text = _extract_pdf_text(pdf, visual_sort=True)
    compiled = _compile_profile_re(profile) if profile else {}
    section = _locate_findings_sections(
        _strip_ocr_html(text),
        extra_break_re=compiled.get("extra_break_re"),
        extra_skip_re=compiled.get("extra_skip_re"),
        extra_anchor_re=compiled.get("extra_anchor_re"),
        anchor_only=bool(compiled.get("anchor_only")))
    if not (section and len(section) > 50):
        return None
    if profile and profile.get("table_conclusion"):
        return _reflow_table_conclusion(section)[:16000]
    section = _reflow_conclusion_lines(section)
    if profile and profile.get("review_block"):
        section = _drop_review_block(section)
    return section[:16000]


def _hash(text):
    return hashlib.sha1(re.sub(r"\s+", "", text or "").encode("utf-8")).hexdigest()[:16]


@pytest.mark.parametrize("key", sorted(MATCH))
def test_conclusion_text_strict(key):
    """重提取结论段 == 验收基线(切段/定位/reflow 回归即红)。"""
    text = _re_extract_text(key)
    assert text is not None, f"{key} 锚点段丢失(基线存在, 应能确定性重提取)"
    got = _hash(text)
    expected = BASELINE[key[0]][key[1]]["conclusion_hash"]
    assert got == expected, (
        f"{key} conclusion_text 与验收基线不一致\n  当前 {got}\n  基线 {expected}")


@pytest.mark.parametrize("key", sorted(HYBRID))
def test_conclusion_text_hybrid_chain_lock(key):
    """生产 hybrid(OCR 补文)链与离线链产物不同: 只锁离线链产物防漂移, 不比对基线。"""
    got = _hash(_re_extract_text(key))
    assert got == HYBRID[key], (
        f"{key} hybrid=False 链产物漂移(锁定 {HYBRID[key]}, 当前 {got})")
    pytest.skip("生产走 hybrid(OCR 补文)链, 与离线链不等价; 基线由三件套 DB 快照守")
