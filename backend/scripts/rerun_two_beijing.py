"""一次性: 重跑 2 份报告(陈美杉 H003 task7 / 步新宇 H004 task6)完整解析+解读。
重置数据 → process_task(新代码: ref 顺序/箭头信号/弃检) → interpretation worker 消费解读。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sqltext

from app.core.database import get_session
from app.modules.report.service import process_task

JOBS = [
    ("hospital_H003", 7, 7),
    ("hospital_H004", 6, 6),
]


def main():
    for db_name, task_id, report_id in JOBS:
        s = get_session(db_name)
        try:
            # 重置 task
            s.execute(sqltext(
                f"UPDATE {db_name}.report_task SET status='pending', error_message=NULL, "
                f"retry_count=0, completed_at=NULL WHERE id=:tid"
            ), {"tid": task_id})
            s.commit()
            # 删旧 interpretation / judgment / indicator
            s.execute(sqltext(f"DELETE FROM {db_name}.report_interpretation WHERE report_id=:rid"), {"rid": report_id})
            s.execute(sqltext(
                f"DELETE FROM {db_name}.indicator_judgment WHERE indicator_id IN "
                f"(SELECT id FROM {db_name}.report_indicator WHERE report_id=:rid)"
            ), {"rid": report_id})
            s.execute(sqltext(f"DELETE FROM {db_name}.report_indicator WHERE report_id=:rid"), {"rid": report_id})
            s.commit()
            print(f"[{db_name}] task{task_id} 数据已重置, 开始解析...", flush=True)
            process_task(s, task_id, db_name[-4:] if False else db_name.replace("hospital_", ""))
            s.commit()
            print(f"[{db_name}] 解析完成, 已投解读队列", flush=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[{db_name}] 失败: {e}", flush=True)
        finally:
            s.close()
    print("完成", flush=True)


if __name__ == "__main__":
    main()
