"""一次性脚本: H004 全部报告 conclusion_text 规整(重切 + 折行重排)并重跑提取落库。

背景(2026-09-03): 结论展示区存在 ①PDF 行内折行 ②页眉/联系方式混入
③签名行/分检报告(体格检查/检验报告)未断点混入结论段。_locate_findings_sections
新增 BREAK/SKIP 断点, 存储前经 _reflow_conclusion_lines 合并折行。
用法:
    .venv/bin/python scripts/reflow_conclusions_h004.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
from sqlalchemy import text

from app.core.database import get_session
from app.modules.report.service import (
    _extract_abnormalities_async,
    _locate_findings_sections,
    _reflow_conclusion_lines,
    _store_abnormalities,
)
from app.modules.interpretation.service import refresh_interpretation_counts

DB = "hospital_H004"
FILES = {
    2: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/6f690cc1882e458d9a3368bedf80e92c.pdf",
    3: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/c2e20cb55e0a4a0981db78710a76dcb0.pdf",
    5: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/2ee3be88a0c94a6f9b9f3fc4828d2e3a.PDF",
    6: "./storage/H001/batch/extracted/84285a049b1f457a8b72dd6846261b45/cc526018f1534e859fbb4eff3e90a7df.pdf",
}


async def main():
    s = get_session(DB)
    try:
        for report_id, path in FILES.items():
            doc = fitz.open(path)
            full_text = "\n".join(p.get_text() for p in doc)
            doc.close()
            section = _locate_findings_sections(full_text)
            if not section:
                print(f"[{report_id}] 段落定位失败", flush=True)
                continue
            polished = _reflow_conclusion_lines(section)
            s.execute(text(f"UPDATE {DB}.report_info SET conclusion_text=:c WHERE id=:rid"),
                      {"c": polished[:16000], "rid": report_id})
            s.commit()
            interp = s.execute(text(f"SELECT id FROM {DB}.report_interpretation WHERE report_id=:rid ORDER BY id DESC LIMIT 1"),
                               {"rid": report_id}).scalar()
            if not interp:
                print(f"[{report_id}] 无 interpretation", flush=True)
                continue
            t0 = time.time()
            items = await _extract_abnormalities_async(polished)
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
            names = [i.get("item_name") for i in items]
            print(f"[{report_id}] 结论 {len(polished)} 字, 提取 {len(items)} 条: {'; '.join(names)}", flush=True)
            print(f"    用时 {time.time()-t0:.0f}s", flush=True)
    finally:
        s.close()


if __name__ == "__main__":
    asyncio.run(main())
