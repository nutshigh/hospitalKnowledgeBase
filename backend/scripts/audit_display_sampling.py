"""展示层一致性抽查(2026-09-05): 旧验收 judgments(黄红区) vs 当前代码提取+同款判定。

模拟 interp_graph 的 triage 判定(signal_flag 通道 + rules_engine ref 判定),
对新提取 indicators 判三色, 与 DB 中验收解读的黄红区名单对比。
"""
import os
import sys

sys.path.insert(0, "/home/wjyy2/hospitalKnowledgeBase/backend")
from app.modules.report.service import (  # noqa: E402
    _compile_profile_re,
    _extract_pdf_text,
    _load_report_profiles,
    _locate_findings_sections,
)
from app.modules.report.table_extractor import (  # noqa: E402
    extract_abnormal_signals,
    extract_column_table_rows,
    extract_indicator_rows,
)
from app.modules.interpretation.rules_engine import RulesEngine  # noqa: E402
from app.core.term_normalizer import normalize_indicators  # noqa: E402
from app.ai.agents.interp_graph import _to_num  # noqa: E402
import pymysql  # noqa: E402

SAMPLES = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例"
CASES = [
    ("hospital_H003", 1, os.path.join(SAMPLES, "广西体检报告测试", "广西崇左市人民医院.pdf")),
    ("hospital_H004", 1, os.path.join(SAMPLES, "广西体检报告测试", "广西柳州市人民医院.pdf")),
    ("hospital_H003", 7, os.path.join(SAMPLES, "陈美杉_H003_10.pdf")),
]


def pipeline(pdf):
    t = _extract_pdf_text(pdf, hybrid=False)
    profile, _ = _load_report_profiles()
    comp = _compile_profile_re(profile(t))
    sec = _locate_findings_sections(
        t, extra_break_re=comp["extra_break_re"],
        extra_skip_re=comp["extra_skip_re"], extra_anchor_re=comp["extra_anchor_re"])
    t2 = t.replace(sec, "") if sec and len(sec) > 50 else t
    rows = extract_indicator_rows(t2)
    col_rows = extract_column_table_rows(t2)
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


def judge_level(ind, engine, hospital):
    """与 interp_graph run_rules 相同判定逻辑(395-470 段)。"""
    ind_dict = {"item_name": ind["item_name"], "item_name_standard": ind.get("item_name_standard"),
                "result_value": ind.get("result"), "unit": ind.get("unit", ""),
                "ref_range_low": ind.get("ref_low"), "ref_range_high": ind.get("ref_high")}
    result = engine.evaluate(hospital, ind_dict)
    deviation = result.deviation
    color_level = result.color_level
    f = ind.get("signal_flag") or 0
    if f == 3 or f == 2:
        color_level = "yellow"
        if deviation == "normal":
            deviation = "abnormal"
    elif f == 1:
        try:
            val = _to_num(ind.get("result"))
            ref_high = _to_num(ind.get("ref_high"))
            ref_low = _to_num(ind.get("ref_low"))
            if ref_high and ref_low and (val > ref_high or val < ref_low):
                deviation = "high" if val > ref_high else "low"
                color_level = "yellow"
            elif not (ref_high or ref_low):
                color_level = "yellow"
        except (ValueError, TypeError):
            color_level = "yellow"
    if deviation == "normal":
        try:
            val = _to_num(ind.get("result"))
            ref_high = _to_num(ind.get("ref_high"))
            if ref_high and val > ref_high:
                deviation = "high"
                if color_level == "green":
                    color_level = "yellow"
            ref_low = _to_num(ind.get("ref_low"))
            if ref_low and val < ref_low:
                deviation = "low"
                if color_level == "green":
                    color_level = "yellow"
        except (ValueError, TypeError):
            pass
    return color_level


def old_yellow_red(db, rid):
    c = pymysql.connect(host="127.0.0.1", user="root", password="root", database=db, charset="utf8mb4")
    cur = c.cursor()
    cur.execute(
        f"""SELECT ij.item_name, ij.color_level FROM {db}.indicator_judgment ij
            JOIN {db}.report_interpretation i ON ij.interpretation_id = i.id
            JOIN {db}.report_indicator ri ON ij.indicator_id = ri.id
            WHERE i.report_id=%s AND ri.raw_text IS NULL
              AND ij.color_level IN ('yellow','red') ORDER BY ij.id""", (rid,))
    out = cur.fetchall()
    c.close()
    return [(n, c2) for n, c2 in out]


def main():
    for db, rid, pdf in CASES:
        old = old_yellow_red(db, rid)
        old_names = [n for n, _ in old]
        inds = pipeline(pdf)
        engine = RulesEngine()
        hosp = db.replace("hospital_", "")
        new_yellow = []
        for ind in inds:
            lv = judge_level(ind, engine, hosp)
            if lv in ("yellow", "red"):
                new_yellow.append((ind["item_name"], ind.get("result"), lv))
        new_names = [n for n, _, _ in new_yellow]
        gone = [n for n in old_names if n not in new_names]
        added = [x for x in new_yellow if x[0] not in old_names]
        print(f"### {db} r{rid}: 旧黄红 {len(old)} / 新黄红 {len(new_yellow)}")
        if gone:
            print("  旧有新增无(可能丢失):", gone[:14])
        if added:
            print("  新增(需核):", added[:14])
        for n, c2 in old:
            if n in new_names and c2 != "yellow":
                pass
        print()


if __name__ == "__main__":
    main()
