"""历史基线审计(2026-09-05): DB 验收产物 vs 当前代码提取, 定位指标层回归。"""
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
from app.core.term_normalizer import normalize_indicators  # noqa: E402
import pymysql  # noqa: E402

SAMPLES = "/home/wjyy2/hospitalKnowledgeBase/体检报告样例"
GX = os.path.join(SAMPLES, "广西体检报告测试")

# (db, report_id, 样本pdf)
CASES = [
    ("hospital_H003", 1, os.path.join(GX, "广西崇左市人民医院.pdf")),
    ("hospital_H003", 2, os.path.join(GX, "广西壮族自治区人民医院.pdf")),
    ("hospital_H003", 3, os.path.join(GX, "广西百色市右江民族医学院附属医院.pdf")),
    ("hospital_H003", 4, os.path.join(GX, "广西贵港市东晖医院.pdf")),
    ("hospital_H003", 5, os.path.join(GX, "广西防城港市中医医院.PDF")),
    ("hospital_H003", 6, os.path.join(GX, "广西防城港市第一人民医院.pdf")),
    ("hospital_H003", 7, os.path.join(SAMPLES, "陈美杉_H003_10.pdf")),
    ("hospital_H004", 1, os.path.join(GX, "广西柳州市人民医院.pdf")),
    ("hospital_H004", 2, os.path.join(GX, "广西桂林市南溪山医院.pdf")),
    ("hospital_H004", 3, os.path.join(GX, "广西梧州市中医医院.pdf")),
    ("hospital_H004", 4, os.path.join(GX, "广西钦州市中医医院.pdf")),
    ("hospital_H004", 5, os.path.join(GX, "广西钦州市第二人民医院.PDF")),
    ("hospital_H004", 6, os.path.join(SAMPLES, "步新宇_H004_11.pdf")),
]


def pipeline(pdf):
    text = _extract_pdf_text(pdf, hybrid=False)
    profile, _ = _load_report_profiles()
    comp = _compile_profile_re(profile(text))
    sec = _locate_findings_sections(
        text, extra_break_re=comp["extra_break_re"],
        extra_skip_re=comp["extra_skip_re"], extra_anchor_re=comp["extra_anchor_re"])
    t2 = text.replace(sec, "") if sec and len(sec) > 50 else text
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
        sig_from_signal = (2 if sig_key in arrow_keys else (1 if sig_key in signal_names else 0))
        best = max(r.get("signal_flag") or 0, row_flag_map.get(sig_key, 0), sig_from_signal)
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


def db_rows(db, rid):
    c = pymysql.connect(host="127.0.0.1", user="root", password="root", database=db, charset="utf8mb4")
    cur = c.cursor()
    cur.execute(f"SELECT item_name, result_value, unit, ref_range_low, ref_range_high, signal_flag "
                f"FROM {db}.report_indicator WHERE report_id=%s AND raw_text IS NULL", (rid,))
    out = cur.fetchall()
    c.close()
    return out


def key_of(nm, res):
    return (nm.strip(), (res or "").strip())


def main():
    for db, rid, pdf in CASES:
        if not os.path.exists(pdf):
            print(f"### {db} r{rid}: 样本缺失 {pdf}")
            continue
        old = db_rows(db, rid)
        new = pipeline(pdf)
        okeys = {key_of(o[0], o[1]): o for o in old}
        nkeys = {key_of(n["item_name"], n.get("result")): n for n in new}
        miss = [o for k, o in okeys.items() if k not in nkeys]
        added = [n for k, n in nkeys.items() if k not in okeys]
        flag_up = []
        flag_down = []
        ref_diff = []
        for k, o in okeys.items():
            n = nkeys.get(k)
            if not n:
                continue
            of, nf = o[5] or 0, n.get("signal_flag") or 0
            if of == 0 and nf > 0:
                flag_up.append((o[0], o[1]))
            elif of > 0 and nf == 0:
                flag_down.append((o[0], o[1]))
            oref = (o[3], o[4])
            nref = (n.get("ref_low"), n.get("ref_high"))
            if oref != nref:
                ref_diff.append((o[0], o[1], oref, nref))
        print(f"### {db} r{rid}: 基线 {len(old)} / 新 {len(new)} "
              f"| 漏 {len(miss)} | 新增 {len(added)} | flag升 {len(flag_up)} | flag降 {len(flag_down)} | ref异 {len(ref_diff)}")
        for o in miss[:8]:
            print(f"   漏: {o[0]} | {o[1]} | u={o[2]} | ref={o[3]}-{o[4]} | f={o[5]}")
        for n in added[:8]:
            print(f"   增: {n['item_name']} | {n.get('result')} | u={n.get('unit')} | ref={n.get('ref_low')}-{n.get('ref_high')} | f={n.get('signal_flag')}")
        for nm, res, oref, nref in ref_diff[:8]:
            print(f"   ref异: {nm} | {res} | {oref} -> {nref}")
        for nm, res in flag_up[:6]:
            print(f"   flag↑: {nm} | {res}")
        for nm, res in flag_down[:6]:
            print(f"   flag↓: {nm} | {res}")


if __name__ == "__main__":
    main()
