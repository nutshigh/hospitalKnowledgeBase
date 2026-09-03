"""一次性脚本: H003 6 份报告指标/结论全量重建。
1. 指标: 挖结论段后重新提取, 回填 ref; 删除结论污染行(result_value NULL 且无 ref 且无 raw_text)
2. 结论: 重新提取总检异常落库(含跨线方向词去重)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
from sqlalchemy import text as sqltext

from app.core.database import get_session
from app.modules.report.service import _extract_abnormalities_async, _locate_findings_sections, _store_abnormalities
from app.modules.report.table_extractor import extract_indicator_rows

DB = "hospital_H003"
FILES = {
    1: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/d754cad74aa04299a75679097bc12aca.pdf",
    2: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/4ad477f4325043fc83ab79c74ea0055f.pdf",
    3: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/f29a7497cb584baea0d0a136ab3c3a3d.pdf",
    4: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/865d3ebef26342feb01a5c10ba50e5d9.pdf",
    5: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/c163599a292849ed8cacd6b9ac91afdf.PDF",
    6: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/f652a4e220d744d6992cd09d28b05c91.pdf",
}


def main():
    s = get_session(DB)
    try:
        for report_id, path in FILES.items():
            doc = fitz.open(path)
            full = "\n".join(p.get_text() for p in doc)
            doc.close()
            sec = _locate_findings_sections(full) or ""
            ind_text = full.replace(sec, "") if len(sec) > 50 else full

            # ---- 1. 指标: 回填 ref ----
            rows = extract_indicator_rows(ind_text)
            updated = 0
            for r in rows:
                lo, hi = r.get("ref_low"), r.get("ref_high")
                if not (lo or hi):
                    continue
                res = s.execute(sqltext(
                    f"UPDATE {DB}.report_indicator SET ref_range_low=:lo, ref_range_high=:hi "
                    f"WHERE report_id=:rid AND item_name=:nm AND result_value=:rv"),
                    {"lo": lo, "hi": hi, "rid": report_id, "nm": r["item_name"], "rv": r["result"]})
                updated += res.rowcount
            s.commit()

            # ---- 2. 删除结论污染行(result NULL + 无 ref + 无 raw_text)及其 judgment ----
            del_res = s.execute(sqltext(
                f"DELETE FROM {DB}.indicator_judgment WHERE indicator_id IN ("
                f"  SELECT id FROM {DB}.report_indicator "
                f"  WHERE report_id=:rid AND result_value IS NULL AND raw_text IS NULL "
                f"    AND ref_range_low IS NULL AND ref_range_high IS NULL)"),
                {"rid": report_id})
            del_ri = s.execute(sqltext(
                f"DELETE FROM {DB}.report_indicator "
                f"WHERE report_id=:rid AND result_value IS NULL AND raw_text IS NULL "
                f"  AND ref_range_low IS NULL AND ref_range_high IS NULL"),
                {"rid": report_id})
            # 2026-08-31: 建议文本误提取的指标行(名称含"门诊/弹性成像/CAP值")删除
            # (崇左"脂肪肝健康管理门诊" 会误拦结论"脂肪肝")
            del_res2 = s.execute(sqltext(
                f"DELETE FROM {DB}.indicator_judgment WHERE indicator_id IN ("
                f"  SELECT id FROM {DB}.report_indicator "
                f"  WHERE report_id=:rid AND (item_name LIKE '%门诊%' OR item_name LIKE '%弹性成像%' "
                f"    OR item_name LIKE '%CAP值%'))"),
                {"rid": report_id})
            del_ri2 = s.execute(sqltext(
                f"DELETE FROM {DB}.report_indicator "
                f"WHERE report_id=:rid AND (item_name LIKE '%门诊%' OR item_name LIKE '%弹性成像%' "
                f"  OR item_name LIKE '%CAP值%')"),
                {"rid": report_id})
            s.commit()

            # ---- 3. 结论重跑 ----
            interp = s.execute(sqltext(
                f"SELECT id FROM {DB}.report_interpretation WHERE report_id=:rid ORDER BY id DESC LIMIT 1"),
                {"rid": report_id}).scalar()
            if not interp:
                print(f"[{report_id}] 无 interpretation", flush=True)
                continue
            items = asyncio.run(_extract_abnormalities_async(sec))
            s.execute(sqltext(
                f"DELETE ij FROM {DB}.indicator_judgment ij "
                f"JOIN {DB}.report_indicator ri ON ij.indicator_id=ri.id "
                f"WHERE ij.interpretation_id=:iid AND ri.raw_text IS NOT NULL"),
                {"iid": interp})
            s.execute(sqltext(
                f"DELETE FROM {DB}.report_indicator WHERE raw_text IS NOT NULL AND id NOT IN "
                f"(SELECT indicator_id FROM {DB}.indicator_judgment)"))
            s.commit()
            if items:
                _store_abnormalities(s, report_id, interp, items)
                s.commit()
            # ---- 4. 同步 interp 红/黄区统计(结论重跑后快照过时) ----
            stats = s.execute(sqltext(
                f"SELECT "
                f"  SUM(CASE WHEN ij.color_level='red' THEN 1 ELSE 0 END), "
                f"  SUM(CASE WHEN ij.color_level='yellow' THEN 1 ELSE 0 END) "
                f"FROM {DB}.indicator_judgment ij WHERE ij.interpretation_id=:iid"),
                {"iid": interp}).fetchone()
            red_n, yellow_n = int(stats[0] or 0), int(stats[1] or 0)
            overall = "red" if red_n else ("yellow" if yellow_n else "green")
            s.execute(sqltext(
                f"UPDATE {DB}.report_interpretation SET red_count=:r, yellow_count=:y, overall_level=:o "
                f"WHERE id=:iid"),
                {"r": red_n, "y": yellow_n, "o": overall, "iid": interp})
            s.commit()
            names = [it.get("item_name", "") for it in items]
            print(f"[{report_id}] 指标ref更新{updated}行, 污染删judgment={del_res.rowcount}行指标={del_ri.rowcount}行, 门诊行judgment={del_res2.rowcount}指标={del_ri2.rowcount}, "
                  f"结论{len(items)}条: {'; '.join(names)}", flush=True)
    finally:
        s.close()
    print("完成", flush=True)


if __name__ == "__main__":
    main()
