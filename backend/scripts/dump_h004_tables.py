"""诊断 harness(2026-09-07): 复现 H004 USER6 7 份报告的表提取产物。
对每份: 分页文本 -> 挖结论段(production 同流程) -> extract_indicator_rows /
extract_column_table_rows -> dump JSON 与关键上下文文本, 供逐行核对。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.report.service import _extract_pdf_text, _locate_findings_sections
from app.modules.report.report_profiles import match_profile, compile_profile
from app.modules.report.table_extractor import (
    extract_indicator_rows, extract_column_table_rows,
)

PDFS = {
    21: "./storage/H001/batch/extracted/b4825d7835754fa290853abedb36a536/ff8617b229f74f0db3ffdb75ddbc8d96.pdf",
    22: "./storage/H001/batch/extracted/b4825d7835754fa290853abedb36a536/ab88be048f77409fb6d49ab7c27966dd.pdf",
    23: "./storage/H001/batch/extracted/b4825d7835754fa290853abedb36a536/0d5933a0c234437d8f04ffd6e0c904ff.pdf",
    24: "./storage/H001/batch/extracted/b4825d7835754fa290853abedb36a536/0a484c03c2dc41a2b32d4f2eaa066a72.pdf",
    25: "./storage/H001/batch/extracted/b4825d7835754fa290853abedb36a536/17ef6cc1e6e843ac9e3444707a6758e8.pdf",
    26: "./storage/H001/batch/extracted/b4825d7835754fa290853abedb36a536/30b924ec1ca1484694aeb5b6374b60f9.pdf",
    27: "./storage/H001/batch/extracted/b4825d7835754fa290853abedb36a536/cf508d98aae54f0e9cd7d0fbf863d932.pdf",
}
OUT = Path("/home/wjyy2/logs/h004_diag")


def main(rids):
    OUT.mkdir(parents=True, exist_ok=True)
    for rid in rids:
        path = PDFS[rid]
        text = _extract_pdf_text(path)
        (OUT / f"{rid}_pages.txt").write_text(text, encoding="utf-8")
        profile = match_profile(text)
        compiled = compile_profile(profile) if profile else {}
        sec = _locate_findings_sections(
            text, extra_break_re=compiled.get("extra_break_re"),
            extra_skip_re=compiled.get("extra_skip_re"),
            extra_anchor_re=compiled.get("extra_anchor_re"))
        stripped = text
        if sec and len(sec) > 50:
            stripped = text.replace(sec, "")
        rows = extract_indicator_rows(stripped)
        cols = extract_column_table_rows(stripped)
        (OUT / f"{rid}_rows.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        (OUT / f"{rid}_cols.json").write_text(
            json.dumps(cols, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{rid}] pages={text.count('--- Page')} 行式rows={len(rows)} 列式cols={len(cols)} "
              f"findings_sec={len(sec or '')}", flush=True)


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]] or list(PDFS))
