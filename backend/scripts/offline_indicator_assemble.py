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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql

from app.modules.report.service import (
    _compile_profile_re, _extract_pdf_text, _load_report_profiles,
    _locate_findings_sections, _remove_findings_section, _retarget_signal_name,
)
from app.modules.report.table_extractor import (
    apply_redref_flags, col_rows_with_fallback, extract_abnormal_signals,
    extract_indicator_rows, extract_personal_info,
)
from app.core.term_normalizer import normalize_indicators


def assemble(pdf_path: str, hybrid: bool = False, force_ocr: bool = False,
             text_file: str = None) -> list[dict]:
    # 2026-10-16: --force-ocr(文本层乱码件, 陈宏宏) / --text-file(复用已缓存 OCR 全文,
    # 避免反复触发 OCR 长跑)
    if text_file:
        text = open(text_file, encoding="utf-8").read()
    else:
        text = _extract_pdf_text(pdf_path, hybrid=hybrid, force_ocr=force_ocr)
    match_profile, _compile = _load_report_profiles()
    profile = match_profile(text)
    compiled = _compile_profile_re(profile) if profile else {}
    if profile and (profile.get("visual_sort") or profile.get("visual_lines")):
        text = _extract_pdf_text(pdf_path, visual_sort=True,
                                 visual_lines=bool(profile.get("visual_lines")))
    # 2026-09-28: 档案声明 ocr_content_pages(蔡明) —— hybrid 模式对宽条内容图页补 OCR
    if hybrid and profile.get("ocr_content_pages"):
        _ocr_text = _extract_pdf_text(pdf_path, hybrid=True, ocr_content=True)
        if len(_ocr_text or "") > len(text):
            text = _ocr_text
            profile = match_profile(text)
    # 挖结论段(与 process_task 一致)
    findings_sec = _locate_findings_sections(
        text,
        extra_break_re=compiled.get("extra_break_re"),
        extra_skip_re=compiled.get("extra_skip_re"),
        extra_anchor_re=compiled.get("extra_anchor_re"))
    if findings_sec and len(findings_sec) > 50:
        text = _remove_findings_section(text, findings_sec)

    rows = extract_indicator_rows(
        text, reversed_dump=bool(profile.get("reversed_dump")) if profile else False,
        latin_wrap=bool(profile.get("latin_wrap")) if profile else False)
    col_rows = col_rows_with_fallback(pdf_path, text)
    signals = extract_abnormal_signals(
        pdf_path, skip_text=findings_sec or "",
        red_marks=bool(profile.get("red_marks")) if profile else False,
        red_ref=bool(profile.get("red_marks_ref")) if profile else False)
    # 2026-09-28: 信号通道误配折行名碎片(与 service.process_task 同构)
    _name_pool = {(r["item_name"], (r["result"] or "").strip()) for r in col_rows}
    _name_pool |= {(r["item_name"], (r["result"] or "").strip()) for r in rows}
    _full_by_result: dict = {}
    for _r in list(col_rows) + list(rows):
        _full_by_result.setdefault(
            (_r.get("result") or "").strip(), []).append(_r["item_name"])
    signals = [
        dict(_s, item_name=_retarget_signal_name(
            _s.get("item_name") or "", _s.get("result") or "",
            _name_pool, _full_by_result))
        for _s in signals
    ]
    # 2026-10-16: 信号通道把"参考值(阴性)+↑"配成信号行(陈坤铭 尿胆原) —— 同名
    # 规则行已有不同结果且信号结果是 阴性/正常 类参考词 → 丢弃(与 service 同构)
    _rule_res = {(r["item_name"]): (r.get("result") or "").strip()
                 for r in list(col_rows) + list(rows)}
    signals = [
        _s for _s in signals
        if not (_rule_res.get(_s.get("item_name"))
                and _rule_res[_s["item_name"]] != (_s.get("result") or "").strip()
                and re.fullmatch(r"(?:阴性|正常|未见异常|未见明显异常|[-—–])",
                                 (_s.get("result") or "").strip()))
    ]
    # 2026-10-14: 倒序转储医院(江西边检) —— 信号通道反序表区错配守卫(与 service 同构)
    if profile and profile.get("reversed_dump"):
        _num_by_name = {}
        for _r0 in list(col_rows) + list(rows):
            _rv0 = (_r0.get("result") or "").strip()
            if re.search(r"\d", _rv0):
                _num_by_name.setdefault(_r0["item_name"], _rv0)
        signals = [
            _s for _s in signals
            if not (_num_by_name.get(_s.get("item_name"))
                    and _num_by_name[_s["item_name"]] != (_s.get("result") or "").strip()
                    and not re.search(r"\d", (_s.get("result") or "")))
        ]

    col_flag_keys = {
        (r["item_name"], r["result"]) for r in col_rows if r.get("signal_flag") == 3
    }
    if col_flag_keys:
        signals = [s for s in signals if (s["item_name"], s["result"]) not in col_flag_keys]
    signal_names = {(s["item_name"], s["result"]) for s in signals
                    if s.get("signal") != "redref"}
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
    # 2026-09-28: 名称折行续行补全(与 service.process_task 同构) —— 列式截断名是行式
    # 全名前缀且结果相同时采用全名(曾繁鲁反馈黄区名被拆两条)
    _row_full_names: dict = {}
    for _r in rows:
        _row_full_names.setdefault(
            (_r.get("result") or "").strip(), []).append(_r["item_name"])
    for r in col_rows:
        if r["item_name"] not in auth_names:
            for _full in _row_full_names.get((r.get("result") or "").strip(), []):
                if len(_full) > len(r["item_name"]) and _full.startswith(r["item_name"]):
                    r = dict(r)
                    r["item_name"] = _full
                    break
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
                def _f(v):
                    try:
                        return float(str(v))
                    except (TypeError, ValueError):
                        return None
                _clo, _chi = _f(r.get("ref_low")), _f(r.get("ref_high"))
                _rlo, _rhi = _f(_rr.get("ref_low")), _f(_rr.get("ref_high"))
                _bad = (_clo is not None and _chi is not None and _chi <= _clo)
                _rhi_s = str(_rr.get("ref_high") or "").strip()
                _chi_s = str(r.get("ref_high") or "").strip()
                _richer = bool(_chi is not None and _rhi is not None
                               and _rhi > _chi and _chi_s and _rhi_s
                               and _rhi_s.startswith(_chi_s) and _rhi_s != _chi_s)
                if ((not r.get("ref_low") and not r.get("ref_high"))
                        or _bad or _richer) \
                        and _rlo is not None and _rhi is not None and _rhi > _rlo:
                    r["ref_low"], r["ref_high"] = _rr.get("ref_low"), _rr.get("ref_high")
                _cu = str(r.get("unit") or "").strip()
                _ru = str(_rr.get("unit") or "").strip()
                if _ru and (not _cu or (_cu.endswith("/") and _ru.startswith(_cu)
                                       and len(_ru) > len(_cu))):
                    r["unit"] = _rr["unit"]
                    r["unit"] = _rr.get("unit")
        indicators.append(r)
    for r in rows:
        sig_key = (r["item_name"], r["result"])
        if r["item_name"] in auth_names:
            continue
        if sig_key in col_keys:
            continue
        # 2026-10-19(韩先锋): 行式 result+unit == 列式 result → 列式更完整, 不重复建行
        _row_full = (str(r.get("result") or "").strip() + " "
                     + str(r.get("unit") or "").strip()).strip()
        if any((c.get("item_name") or "").strip() == r["item_name"]
               and str(c.get("result") or "").strip() == _row_full
               for c in col_rows):
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
    # 2026-10-07: 结论段来源信号只标黄既有行, 不新建行(与 service 同构)
    _csrc_only = (
        {(s["item_name"], s["result"]) for s in signals if s.get("_conclusion_src")}
        - {(s["item_name"], s["result"]) for s in signals if not s.get("_conclusion_src")})
    for s_key in signal_names:
        if s_key[0] in auth_names:
            continue
        if s_key in _csrc_only:
            continue
        if not (s_key[1] or "").strip():
            continue  # 2026-09-23: 空结果信号行不入库(与 service 同构)
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
    # 2026-10-19: 总结段异常项给既有行标黄(档案 summary_marks, 与 service 同构)
    if profile and profile.get("summary_marks") and findings_sec:
        from app.modules.report.table_extractor import mark_indicators_by_summary
        mark_indicators_by_summary(indicators, findings_sec)
    # 2026-09-23: 姓名同名行清理(与 service.process_task 同构; 曹嘉冰封面"曹嘉冰/44")
    from app.modules.report.table_extractor import (
        extract_personal_info as _epi, extract_name_from_layout as _enfl, _name_ok)
    _pname = (_epi(text).get("name") or "").strip()
    _lay = _enfl(pdf_path)
    if _lay and _name_ok(_lay):
        _pname = _lay
    if _pname:
        indicators = [i for i in indicators
                      if (i.get("item_name") or "").strip() != _pname]
    # 2026-10-13: 与 service 同构 —— ocr_content_pages 档案(漳州市医院)的 OCR-HTML
    # 表并入(结论标题/化验小图表; 生产走 process_task 同名分支)
    if profile.get("ocr_content_pages"):
        try:
            from app.core.vlm_client import _parse_markdown_table as _pmt_ocr
            _tbl = normalize_indicators(_pmt_ocr(text)) or []
            _by = {re.sub(r"\s+", "", x.get("item_name", "")): x
                   for x in indicators if x.get("item_name")}
            for _x in _tbl:
                _k = re.sub(r"\s+", "", _x.get("item_name") or "")
                if not _k:
                    continue
                _cur = _by.get(_k)
                if _cur is None:
                    indicators.append(_x)
                    _by[_k] = _x
                    continue
                if (_x.get("signal_flag") or 0) > (_cur.get("signal_flag") or 0):
                    _cur["signal_flag"] = _x.get("signal_flag")
                for _fld in ("ref_low", "ref_high", "unit"):
                    if not _cur.get(_fld) and _x.get(_fld):
                        _cur[_fld] = _x[_fld]
        except Exception:
            pass
    for _ind4 in indicators:
        _r4 = _ind4.get("result")
        if isinstance(_r4, str) and ("＋" in _r4 or "－" in _r4 or "−" in _r4):
            _ind4["result"] = (_r4.replace("＋", "+").replace("－", "-")
                               .replace("−", "-"))
    # 2026-10-13: 同名值形态差兜底上色 —— 恩施红字信号 "+-"(清 CaN 后)与表格行值
    # "+-    Ca10" 精确键不中, 标志落在重复行被去重丢弃(尿潜血/尿白细胞曾漏黄);
    # 按**名称**补标志(红字/箭头是报告方标注, 名称即指标; rev/dual 块表 auth 行不参与)。
    _sig_f_by_name: dict = {}
    for _s in signals:
        _n = (_s.get("item_name") or "").strip()
        if not _n or _s.get("signal") == "redref":
            continue
        _f = 2 if _s.get("signal") == "arrow" else 1
        _sig_f_by_name[_n] = max(_sig_f_by_name.get(_n, 0), _f)
    for _r in indicators:
        if _r.get("item_name") in auth_names:
            continue
        _f = _sig_f_by_name.get((_r.get("item_name") or "").strip(), 0)
        if _f and (_r.get("signal_flag") or 0) < _f:
            _r["signal_flag"] = _f
    indicators = normalize_indicators(indicators)
    # 2026-10-19: 红字+超参考 → f1(望京, 与 service 同构)
    apply_redref_flags(indicators, signals)
    return indicators


def main():
    pdf = sys.argv[1]
    hybrid = "--hybrid" in sys.argv
    force_ocr = "--force-ocr" in sys.argv
    text_file = None
    if "--text-file" in sys.argv:
        text_file = sys.argv[sys.argv.index("--text-file") + 1]
    report_id = None
    if "--report-id" in sys.argv:
        report_id = int(sys.argv[sys.argv.index("--report-id") + 1])
    inds = assemble(pdf, hybrid=hybrid, force_ocr=force_ocr, text_file=text_file)
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
