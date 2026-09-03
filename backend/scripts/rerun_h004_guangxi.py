"""一次性脚本: USER6(H004) 桂林/梧州/钦州第二 重新切结论段并提取落库。

背景(2026-09-03):
- report 2 桂林: 旧 conclusion_text 只切到"健康指导建议"(纯科普) → 体检综述条目全丢
- report 3 梧州: "体检结论汇总"(各科罗列)污染 + 旧进程代码过期 → 提取为空
- report 5 钦州第二: "右肾强光团,钙化灶?结石?" 候选被拆条 + 牙面色素沉着兜底词缺失
用法:
    .venv/bin/python scripts/rerun_h004_guangxi.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
from sqlalchemy import text

from app.core.database import get_session
from app.modules.report.service import _extract_abnormalities_async, _locate_findings_sections, _store_abnormalities
from app.modules.interpretation.service import refresh_interpretation_counts

DB = "hospital_H004"
FILES = {
    2: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/6f690cc1882e458d9a3368bedf80e92c.pdf",
    3: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/c2e20cb55e0a4a0981db78710a76dcb0.pdf",
    5: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/2ee3be88a0c94a6f9b9f3fc4828d2e3a.PDF",
}


async def main():
    s = get_session(DB)
    try:
        for report_id, path in FILES.items():
            doc = fitz.open(path)
            full_text = "\n".join(p.get_text() for p in doc)
            doc.close()
            section = _locate_findings_sections(full_text) or ""
            if not section:
                print(f"[{report_id}] 段落定位失败, 跳过", flush=True)
                continue
            s.execute(text(f"UPDATE {DB}.report_info SET conclusion_text=:c WHERE id=:rid"),
                      {"c": section[:16000], "rid": report_id})
            s.commit()
            interp = s.execute(text(f"SELECT id FROM {DB}.report_interpretation WHERE report_id=:rid ORDER BY id DESC LIMIT 1"),
                               {"rid": report_id}).scalar()
            if not interp:
                print(f"[{report_id}] 无 interpretation, 跳过", flush=True)
                continue
            t0 = time.time()
            items = await _extract_abnormalities_async(section)
            # 删除旧结论条目 + 孤儿
            s.execute(text(f"DELETE ij FROM {DB}.indicator_judgment ij "
                           f"JOIN {DB}.report_indicator ri ON ij.indicator_id=ri.id "
                           f"WHERE ij.interpretation_id=:iid AND ri.raw_text IS NOT NULL"),
                      {"iid": interp})
            s.execute(text(f"DELETE FROM {DB}.report_indicator WHERE raw_text IS NOT NULL "
                           f"AND id NOT IN (SELECT indicator_id FROM {DB}.indicator_judgment)"))
            s.commit()
            if items:
                _store_abnormalities(s, report_id, interp, items)
                s.commit()
                refresh_interpretation_counts(s, interp)
                s.commit()
                print(f"[{report_id}] 提取 {len(items)} 条, 用时 {time.time()-t0:.0f}s", flush=True)
            else:
                print(f"[{report_id}] 提取为空", flush=True)
    finally:
        s.close()


if __name__ == "__main__":
    asyncio.run(main())
