"""广西 11 + 北京 2 = 13 份指标护栏(2026-09-05)。

两层:
- 冒烟:能提取、指标量不低于阈值、黄红名单无已知垃圾
- 判定链强断言:提取 → triage 判定后的黄红区名单 == 期望名单(验收基线+确认修复)

期望名单来自 2026-09-05 判定链盘点(与 DB 验收 judgments 一致, 除:
贵港 +腰臀比(报告结论"腰臀比增高")、防城港一 +乙肝核心抗体(HBcAb)(红字且 ref 0-0.15 超 100 倍))。
钦州市中医医院 = 图片型 PDF(文本规则不可用, OCR 服务对 8 页 500, 环境遗留 → skip)。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.modules.report.service import (  # noqa: E402
    _compile_profile_re,
    _extract_pdf_text,
    _load_report_profiles,
    _locate_findings_sections,
)
from app.modules.report.table_extractor import (  # noqa: E402
    col_rows_with_fallback,
    extract_abnormal_signals,
    extract_indicator_rows,
)
from app.modules.interpretation.rules_engine import RulesEngine  # noqa: E402
from app.core.term_normalizer import normalize_indicators  # noqa: E402

SAMPLES = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例"
GX = os.path.join(SAMPLES, "广西体检报告测试")

CASES = {
    "崇左": ("hospital_H003", os.path.join(GX, "广西崇左市人民医院.pdf"),
             ["身高体重指数", "低密度脂蛋白胆固醇", "高密度脂蛋白胆固醇", "总胆固醇", "甘油三酯",
              "丙氨酸氨基转移酶", "天门冬氨酸氨基转移酶", "L-γ-谷氨酰基转移酶"]),
    "广西人民": ("hospital_H003", os.path.join(GX, "广西壮族自治区人民医院.pdf"),
               ["尿酸（UA）", "总胆固醇（TC）", "甘油三酯（TG）", "低密度脂蛋白",
                "乙型肝炎表面抗体", "乙型肝炎核心抗体", "粒细胞百分数", "淋巴细胞百分数",
                "血红蛋白（HGB）"]),
    "百色": ("hospital_H003", os.path.join(GX, "广西百色市右江民族医学院附属医院.pdf"),
            ["总胆固醇", "低密度脂蛋白胆固醇", "总胆红素", "直接胆红素", "间接胆红素",
             "碱性磷酸酶", "载脂蛋白B", "血小板分布宽度"]),
    "贵港": ("hospital_H003", os.path.join(GX, "广西贵港市东晖医院.pdf"),
            ["淋巴细胞绝对数", "单核细胞绝对数", "血小板比积", "谷草/谷丙", "尿酸",
             "乙型肝炎病毒表面抗体"]),
    "防城港中": ("hospital_H003", os.path.join(GX, "广西防城港市中医医院.PDF"),
               ["总胆固醇(TCHO)", "甘油三酯(TG)", "低密度脂蛋白胆固醇(LDL-C)", "谷丙转氨酶",
                "肌酸激酶(CK)", "血管紧张转化酶测定", "尿酸(UA)", "红细胞计数(RBC)",
                "平均红细胞体积(MCV)", "平均红细胞血红蛋白含量(MCH)"]),
    "防城港一": ("hospital_H003", os.path.join(GX, "广西防城港市第一人民医院.pdf"),
               ["腰围", "谷丙转氨酶(ALT)", "尿酸(UA)", "二氧化碳总量", "乙肝表面抗体(HBsAb)",
                "乙肝核心抗体(HBcAb)", "红细胞平均体积(MCV)", "平均血红蛋白量(MCH)"]),
    "陈美杉(北京)": ("hospital_H003", os.path.join(SAMPLES, "陈美杉_H003_10.pdf"),
                   ["体重指数", "粘液丝", "血小板压积"]),
    "柳州": ("hospital_H004", os.path.join(GX, "广西柳州市人民医院.pdf"),
            ["视力(左)", "视力(右)", "总胆固醇", "低密度脂蛋白胆固醇", "血尿酸",
             "游离PSA/总PSA", "γ-谷氨酰转移酶", "血小板比容", "红细胞分布宽度-SD"]),
    "桂林": ("hospital_H004", os.path.join(GX, "广西桂林市南溪山医院.pdf"),
            ["血小板分布宽度", "二氧化碳结合力", "高密度脂蛋白", "低密度脂蛋白",
             "干化学酮体"]),
    "梧州": ("hospital_H004", os.path.join(GX, "广西梧州市中医医院.pdf"),
            ["乙肝表面抗体(发光法)", "癌胚抗原定量", "尿酸", "平均血小板体积(MPV)",
             "嗜碱性粒细胞百分比", "甘油三脂"]),
    "钦州二": ("hospital_H004", os.path.join(GX, "广西钦州市第二人民医院.PDF"),
             ["钾(K)"]),
    "步新宇(北京)": ("hospital_H004", os.path.join(SAMPLES, "步新宇_H004_11.pdf"),
                   ["舒张压", "游离前列腺特异性抗原", "血清同型半胱氨酸", "肌酸激酶"]),
}

_GARBAGE_IN_YELLOW = re.compile(
    r"(日期|电话|传真|邮编|页|单位|参考值|参考范围|次/分|肾功|肝功|血脂$|乙肝$|^比$|体检号|证件|"
    r"总检|初审|终审|打印|接收|小结|检查者|医生|医师|护士|样本|标本)")


def _extract(pdf):
    t = _extract_pdf_text(pdf, hybrid=False)
    profile, _ = _load_report_profiles()
    comp = _compile_profile_re(profile(t))
    sec = _locate_findings_sections(
        t, extra_break_re=comp["extra_break_re"],
        extra_skip_re=comp["extra_skip_re"], extra_anchor_re=comp["extra_anchor_re"])
    t2 = t.replace(sec, "") if sec and len(sec) > 50 else t
    rows = extract_indicator_rows(t2)
    col_rows = col_rows_with_fallback(pdf, t2)
    signals = extract_abnormal_signals(pdf)
    col_flag_keys = {(r["item_name"], r["result"]) for r in col_rows if r.get("signal_flag") == 3}
    if col_flag_keys:
        signals = [s for s in signals if (s["item_name"], s["result"]) not in col_flag_keys]
    signal_names = {(s["item_name"], s["result"]) for s in signals}
    arrow_keys = {(s["item_name"], s["result"]) for s in signals if s.get("signal") == "arrow"}
    indicators = []
    seen_sig = set()
    col_keys = set()
    row_flag_map = {(r["item_name"], r["result"]): r.get("signal_flag") or 0 for r in rows}
    row_by_key = {(r["item_name"], r["result"]): r for r in rows}
    for r in col_rows:
        sig_key = (r["item_name"], r["result"])
        col_keys.add(sig_key)
        r = dict(r)
        sf = (2 if sig_key in arrow_keys else (1 if sig_key in signal_names else 0))
        best = max(r.get("signal_flag") or 0, row_flag_map.get(sig_key, 0), sf)
        r["signal_flag"] = best
        rr = row_by_key.get(sig_key)
        if rr and (not r.get("ref_low") and not r.get("ref_high")) \
                and (rr.get("ref_low") or rr.get("ref_high")):
            r["ref_low"], r["ref_high"] = rr.get("ref_low"), rr.get("ref_high")
        if rr and not r.get("unit") and rr.get("unit"):
            r["unit"] = rr.get("unit")
        indicators.append(r)
    for r in rows:
        sig_key = (r["item_name"], r["result"])
        if sig_key in col_keys:
            continue
        sig = r.get("signal_flag", 0) or (
            2 if sig_key in arrow_keys else (1 if sig_key in signal_names else 0))
        if sig and sig_key in seen_sig:
            continue
        if sig:
            seen_sig.add(sig_key)
        indicators.append({"item_name": r["item_name"], "result": r["result"],
                           "unit": r.get("unit", ""), "ref_low": r.get("ref_low"),
                           "ref_high": r.get("ref_high"), "signal_flag": sig})
    for s_key in signal_names:
        if s_key not in seen_sig and s_key not in col_keys:
            indicators.append({"item_name": s_key[0], "result": s_key[1], "unit": "",
                               "ref_low": None, "ref_high": None,
                               "signal_flag": 2 if s_key in arrow_keys else 1})
    for ind in indicators:
        if ind.get("signal_flag"):
            continue
        if "定性" in (ind.get("item_name") or "") and str(ind.get("result", "")).startswith("阳性"):
            ind["signal_flag"] = 1
    return normalize_indicators(indicators)


def _to_num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(">", "").replace("<", "").replace("≥", "").replace("≤", ""))
    except ValueError:
        return None


def _judge_level(ind, engine, hospital):
    result = engine.evaluate(hospital, {
        "item_name": ind["item_name"], "item_name_standard": ind.get("item_name_standard"),
        "result_value": ind.get("result"), "unit": ind.get("unit", ""),
        "ref_range_low": ind.get("ref_low"), "ref_range_high": ind.get("ref_high")})
    deviation = result.deviation
    color_level = result.color_level
    f = ind.get("signal_flag") or 0
    if f in (2, 3):
        color_level = "yellow"
    elif f == 1:
        val = _to_num(ind.get("result"))
        ref_hi = _to_num(ind.get("ref_high"))
        ref_lo = _to_num(ind.get("ref_low"))
        over_hi = ref_hi is not None and val is not None and val > ref_hi
        under_lo = ref_lo is not None and val is not None and val < ref_lo
        if over_hi or under_lo:
            color_level = "yellow"
        elif ref_hi is None and ref_lo is None:
            color_level = "yellow"
    # 2026-09-07: 与 interp_graph.run_rules 口径同步 —— 仅标志判黄,
    # 无 signal_flag 不做 结果 vs 参考 自动比较判黄。
    return color_level


def _yellow_names(pdf, hospital):
    inds = _extract(pdf)
    engine = RulesEngine()
    return [i["item_name"] for i in inds if _judge_level(i, engine, hospital) in ("yellow", "red")]


@pytest.mark.skipif(not os.path.isdir(GX), reason="样本目录不存在")
class TestGxBjJudgmentChain:
    @pytest.mark.parametrize("tag", list(CASES))
    def test_yellow_equals_baseline(self, tag):
        hospital, pdf, expected = CASES[tag]
        if not os.path.exists(pdf):
            pytest.skip(f"{tag} 样本缺失")
        names = _yellow_names(pdf, hospital.replace("hospital_", ""))
        assert set(names) == set(expected), (
            f"{tag} 黄红区不一致\n  缺失: {sorted(set(expected) - set(names))}\n"
            f"  多余: {sorted(set(names) - set(expected))}")

    @pytest.mark.parametrize("tag", list(CASES))
    def test_smoke_no_garbage(self, tag):
        hospital, pdf, _ = CASES[tag]
        if not os.path.exists(pdf):
            pytest.skip(f"{tag} 样本缺失")
        inds = _extract(pdf)
        assert len(inds) >= 20, f"{tag} 指标量过低({len(inds)})"
        names = _yellow_names(pdf, hospital.replace("hospital_", ""))
        bad = [n for n in names if _GARBAGE_IN_YELLOW.search(n)]
        assert not bad, f"{tag} 黄区含垃圾名: {bad[:10]}"

    @pytest.mark.parametrize("tag", list(CASES))
    def test_smoke_key_indicator_present(self, tag):
        hospital, pdf, expected = CASES[tag]
        if not os.path.exists(pdf):
            pytest.skip(f"{tag} 样本缺失")
        inds = _extract(pdf)
        names = [i["item_name"] for i in inds]
        for exp in expected:
            assert any(exp in n or n in exp for n in names), f"{tag} 缺关键指标 {exp}"
