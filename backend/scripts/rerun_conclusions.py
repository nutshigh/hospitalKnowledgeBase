"""一次性脚本: 重跑存量报告的总检建议异常提取, 替换旧的低质量结论条目。

用法(在 backend 目录):
    nohup .venv/bin/python scripts/rerun_conclusions.py >> /home/wjyy2/logs/rerun-conclusions.log 2>&1 &

逻辑: 对每个有结论条目的 interpretation, 删除旧结论条目(ij source=conclusion
+ 孤儿 report_indicator), 用当前代码重新提取并落库。结论段落用已存
conclusion_text(不重新做段落定位)。
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.database import get_session
from app.modules.report.service import (
    _clean_conclusion_section,
    _extract_abnormalities_async,
    _store_abnormalities,
)

DBS = ["hospital_H001", "hospital_H002", "hospital_H003", "hospital_H004"]


def _summary(s, db, interp_id):
    return s.execute(text(
        f"SELECT COUNT(*) FROM {db}.indicator_judgment ij "
        f"JOIN {db}.report_indicator ri ON ij.indicator_id=ri.id "
        f"WHERE ij.interpretation_id=:iid AND ri.raw_text IS NOT NULL"
    ), {"iid": interp_id}).scalar()


async def main():
    for db in DBS:
        s = get_session(db)
        try:
            rows = s.execute(text(
                f"SELECT DISTINCT ij.interpretation_id, ri.report_id "
                f"FROM {db}.indicator_judgment ij "
                f"JOIN {db}.report_indicator ri ON ij.indicator_id=ri.id "
                f"WHERE ri.raw_text IS NOT NULL"
            )).fetchall()
            if not rows:
                print(f"[{db}] 无结论条目, 跳过", flush=True)
                continue
            seen_reports = set()
            for interp_id, report_id in rows:
                if report_id in seen_reports:
                    print(f"[{db}] interp={interp_id} 跳过(报告 {report_id} 已有其它 interp 重跑)", flush=True)
                    continue
                seen_reports.add(report_id)
                ct = s.execute(text(
                    f"SELECT conclusion_text FROM {db}.report_info WHERE id=:rid"
                ), {"rid": report_id}).scalar()
                if not ct:
                    print(f"[{db}] report={report_id} interp={interp_id} 无 conclusion_text, 跳过", flush=True)
                    continue
                ct = _clean_conclusion_section(ct)
                if not ct:
                    print(f"[{db}] report={report_id} interp={interp_id} 清洗后无内容, 跳过", flush=True)
                    continue
                old_n = _summary(s, db, interp_id)
                t0 = time.time()
                items = await _extract_abnormalities_async(ct)
                # 删除旧结论条目 + 孤儿 report_indicator
                s.execute(text(
                    f"DELETE ij FROM {db}.indicator_judgment ij "
                    f"JOIN {db}.report_indicator ri ON ij.indicator_id=ri.id "
                    f"WHERE ij.interpretation_id=:iid AND ri.raw_text IS NOT NULL"
                ), {"iid": interp_id})
                s.execute(text(
                    f"DELETE FROM {db}.report_indicator "
                    f"WHERE raw_text IS NOT NULL AND id NOT IN "
                    f"(SELECT indicator_id FROM {db}.indicator_judgment)"
                ))
                s.commit()
                if items:
                    _store_abnormalities(s, report_id, interp_id, items)
                    s.commit()
                names = [it.get("item_name", "") for it in items]
                print(f"[{db}] report={report_id} interp={interp_id} 旧{old_n}条 → 新{len(items)}条 "
                      f"({time.time()-t0:.0f}s): {'; '.join(names[:10])}", flush=True)
                if len(items) > 10:
                    print(f"    ... 共{len(items)}条: {'; '.join(names[10:])}", flush=True)
        finally:
            s.close()
    print("全部完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
