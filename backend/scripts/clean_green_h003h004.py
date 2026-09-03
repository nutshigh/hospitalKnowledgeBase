"""一次性脚本: 清理 H003/H004 绿区垃圾指标行(电话/日期/拼接残段/名称污染)。

只处理绿区(signal_flag=0 且 raw_text IS NULL): 硬垃圾删行(连带 judgment),
名称污染清洗(item_name 剥结果词)。黄/红区指标与总检异常(raw_text 非空)不动。
用法: .venv/bin/python scripts/clean_green_h003h004.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sqltext

from app.core.database import get_session
from app.modules.report.service import _clean_green_indicator

DBS = ["hospital_H003", "hospital_H004"]


def main():
    for db in DBS:
        s = get_session(db)
        try:
            rows = s.execute(sqltext(
                f"SELECT id, item_name, result_value FROM {db}.report_indicator "
                f"WHERE signal_flag = 0 AND raw_text IS NULL ORDER BY id"
            )).fetchall()
            del_ids, upd = [], []
            for rid, name, value in rows:
                clean = _clean_green_indicator(name, value or "")
                if clean is None:
                    del_ids.append(rid)
                elif clean != name:
                    upd.append((clean, rid))
            for rid in del_ids:
                s.execute(sqltext(
                    f"DELETE FROM {db}.indicator_judgment WHERE indicator_id=:rid"
                ), {"rid": rid})
                s.execute(sqltext(
                    f"DELETE FROM {db}.report_indicator WHERE id=:rid"
                ), {"rid": rid})
            for name, rid in upd:
                s.execute(sqltext(
                    f"UPDATE {db}.report_indicator SET item_name=:nm WHERE id=:rid"
                ), {"nm": name, "rid": rid})
            s.commit()
            print(f"[{db}] 绿区行{len(rows)}: 删除{len(del_ids)} 清洗{len(upd)}", flush=True)
        finally:
            s.close()
    print("完成", flush=True)


if __name__ == "__main__":
    main()
