"""一次性脚本: 对 USER5(H003) 6 份报告重新生成 conclusion_text(修复锚点/跨页/错位),
并重新提取总检建议异常落库。用法:
    .venv/bin/python scripts/rerun_h003_conclusions.py
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

DB = "hospital_H003"
FILES = {
    1: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/d754cad74aa04299a75679097bc12aca.pdf",
    2: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/4ad477f4325043fc83ab79c74ea0055f.pdf",
    3: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/f29a7497cb584baea0d0a136ab3c3a3d.pdf",
    4: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/865d3ebef26342feb01a5c10ba50e5d9.pdf",
    5: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/c163599a292849ed8cacd6b9ac91afdf.PDF",
    6: "./storage/H001/batch/extracted/892940c017114b53ac2d20ee292a8ded/f652a4e220d744d6992cd09d28b05c91.pdf",
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
            # 更新 conclusion_text
            s.execute(text(f"UPDATE {DB}.report_info SET conclusion_text=:c WHERE id=:rid"),
                      {"c": section[:16000], "rid": report_id})
            s.commit()
            # 找该报告的 interpretation
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
            names = [it.get("item_name", "") for it in items]
            print(f"[{report_id}] 段落{len(section)}字 → {len(items)}条 ({time.time()-t0:.0f}s): "
                  f"{'; '.join(names)}", flush=True)
    finally:
        s.close()
    print("完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
