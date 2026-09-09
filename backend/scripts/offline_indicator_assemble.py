"""离线指标组装仿真(2026-09-07 起, 中间迭代验收工具)。

与 service.py process_task 的组装段**同构**(rows + 列式/块表 + signals + __auth 抑制 +
normalize_indicators), 秒级复现"提取→组装→净化"的最终产物, 用于提取器改动的
中间迭代验收; 端到端重跑仅在改动收口后做最终验收(一次)。

用法:
    .venv/bin/python scripts/offline_indicator_assemble.py <pdf> [--report-id N] [--hybrid]
  - --report-id: 若给出, 与 DB hospital 当前该 report 的 report_indicator 行集 diff
    (注意 DB 需先跑过 process_task; diff 只覆盖非 raw 指标行)。
  - --hybrid: 走 _extract_pdf_text(hybrid=True)(慢, 会调 OCR 8006); 默认纯文本快验。

⚠ 若修改 service.py 的组装逻辑, 必须同步本脚本(防漂移)。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql

from app.modules.report.service import (
    _compile_profile_re, _extract_pdf_text, _load_report_profiles,
    _locate_findings_sections,
)
from app.modules.report.table_extractor import (
    extract_abnormal_signals, extract_column_table_rows, extract_indicator_rows,
    extract_personal_info,
)
from app.core.term_normalizer import normalize_indicators


def assemble(pdf_path: str, hybrid: bool = False) -> list[dict]:
    text = _extract_pdf_text(pdf_path, hybrid=hybrid)
    match_profile, _compile = _load_report_profiles()
    profile = match_profile(text)
    compiled = _compile_profile_re(profile) if profile else {}
    if profile and profile.get("visual_sort"):
        text = _extract_pdf_text(pdf_path, visual_sort=True)
    # 挖结论段(与 process_task 一致)
    findings_sec = _locate_findings_sections(
        text,
        extra_break_re=compiled.get("extra_break_re"),
        extra_skip_re=compiled.get("extra_skip_re"),
        extra_anchor_re=compiled.get("extra_anchor_re"))
    if findings_sec and len(findings_sec) > 50:
        text = text.replace(findings_sec, "")

    rows = extract_indicator_rows(text)
    col_rows = extract_column_table_rows(text)
    signals = extract_abnormal_signals(pdf_path)

    col_flag_keys = {
        (r["item_name"], r["result"]) for r in col_rows if r.get("signal_flag") == 3
    }
    if col_flag_keys:
        signals = [s for s in signals if (s["item_name"], s["result"]) not in col_flag_keys]
    signal_names = {(s["item_name"], s["result"]) for s in signals}
    arrow_keys = {
        (s["item_name"], s["result"]) for s in signals if s.get("signal") == "arrow"
    }
    # __auth(rev/dual 块表)名称抑制行式/信号
    auth_names = {r["item_name"] for r in col_rows if r.get("__auth")}
    indicators: list[dict] = []
    seen_sig: set = set()
    col_keys: set = set()
    row_flag_map = {
        (r["item_name"], r["result"]): r.get("signal_flag") or 0 for r in rows
    }
    row_by_key = {(r["item_name"], r["result"]): r for r in rows}
    for r in col_rows:
        sig_key = (r["item_name"], r["result"])
        col_keys.add(sig_key)
        r = dict(r)
        r.pop("__auth", None)
        if (r.get("signal_flag") or 0) < row_flag_map.get(sig_key, 0):
            r["signal_flag"] = row_flag_map[sig_key]
        sig_from_signal = (
            (2 if sig_key in arrow_keys else (1 if sig_key in signal_names else 0))
            if r["item_name"] not in auth_names else 0
        )
        if (r.get("signal_flag") or 0) < sig_from_signal:
            r["signal_flag"] = sig_from_signal
        if r["item_name"] not in auth_names:
            _rr = row_by_key.get(sig_key)
            if _rr:
                if (not r.get("ref_low") and not r.get("ref_high")) \
                        and (_rr.get("ref_low") or _rr.get("ref_high")):
                    r["ref_low"], r["ref_high"] = _rr.get("ref_low"), _rr.get("ref_high")
                if not r.get("unit") and _rr.get("unit"):
                    r["unit"] = _rr.get("unit")
        indicators.append(r)
    for r in rows:
        sig_key = (r["item_name"], r["result"])
        if r["item_name"] in auth_names:
            continue
        if sig_key in col_keys:
            continue
        sig = r.get("signal_flag", 0) or (
            2 if sig_key in arrow_keys else (1 if sig_key in signal_names else 0)
        )
        if sig and sig_key in seen_sig:
            continue
        if sig:
            seen_sig.add(sig_key)
        indicators.append({
            "item_name": r["item_name"],
            "result": r["result"],
            "unit": r.get("unit", ""),
            "ref_low": r.get("ref_low"),
            "ref_high": r.get("ref_high"),
            "signal_flag": sig,
        })
    for s_key in signal_names:
        if s_key[0] in auth_names:
            continue
        if s_key not in seen_sig and s_key not in col_keys:
            indicators.append({
                "item_name": s_key[0], "result": s_key[1],
                "unit": "", "ref_low": None, "ref_high": None,
                "signal_flag": 2 if s_key in arrow_keys else 1,
            })
    for _ind in indicators:
        if _ind.get("signal_flag"):
            continue
        if "定性" in (_ind.get("item_name") or "") and str(_ind.get("result", "")).startswith("阳性"):
            _ind["signal_flag"] = 1
    return normalize_indicators(indicators)


def main():
    pdf = sys.argv[1]
    hybrid = "--hybrid" in sys.argv
    report_id = None
    if "--report-id" in sys.argv:
        report_id = int(sys.argv[sys.argv.index("--report-id") + 1])
    inds = assemble(pdf, hybrid=hybrid)
    print(f"assemble: {len(inds)} 行")
    flagged = sorted((i["item_name"], i["result"]) for i in inds if i.get("signal_flag"))
    print(f"带标志(黄候选) {len(flagged)}:")
    for n, r in flagged:
        print("  ", n, r)
    if report_id:
        conn = pymysql.connect(host="127.0.0.1", user="root", password="root",
                               db="hospital_H004")
        cur = conn.cursor()
        cur.execute(
            "SELECT item_name, result_value, ref_range_low, ref_range_high, signal_flag "
            "FROM report_indicator WHERE report_id=%s AND raw_text IS NULL", (report_id,))
        db_rows = {}
        for nm, rv, lo, hi, fl in cur.fetchall():
            db_rows[(nm or "").strip(), str(rv or "")] = (lo, hi, fl or 0)
        conn.close()
        miss = [f"{n}|{r}" for n, r in db_rows if (n, r) not in
                {(i["item_name"], str(i.get("result", ""))) for i in inds}]
        extra = [f"{i['item_name']}|{i.get('result')}" for i in inds
                 if (i["item_name"], str(i.get("result", ""))) not in db_rows]
        print(f"DB 对比(report {report_id}): 仅DB有 {len(miss)} 条: {miss[:10]}")
        print(f"                        仅脚本有 {len(extra)} 条: {extra[:10]}")
    json.dump(inds, open("/home/wjyy2/logs/offline_assemble.json", "w"),
              ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
