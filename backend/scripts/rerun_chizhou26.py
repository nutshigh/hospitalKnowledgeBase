"""端到端重跑 H003 report 26(池州人民 USER5): 清理 -> 重 process -> 重解读 -> 快照验证。

用法(backend 目录):
    nohup .venv/bin/python scripts/rerun_chizhou26.py > /home/wjyy2/logs/chizhou_e2e.log 2>&1 &
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("RABBITMQ_VHOST", "hospital_dev_wjyy2")
os.environ.setdefault("OCR_BASE_URL", "http://localhost:8006")

import pymysql

from app.core.rabbitmq import TaskMessage, rabbitmq

DB = "hospital_H003"
HOSPITAL_ID = "H003"
REPORT_ID = 26
TASK_ID = 26


def conn():
    return pymysql.connect(host="127.0.0.1", user="root", password="root",
                           db=DB, charset="utf8mb4")


def clean(c):
    cur = c.cursor()
    cur.execute("SELECT id FROM report_interpretation WHERE report_id=%s", (REPORT_ID,))
    for (iid,) in cur.fetchall():
        cur.execute("DELETE FROM indicator_judgment WHERE interpretation_id=%s", (iid,))
    cur.execute("DELETE FROM report_interpretation WHERE report_id=%s", (REPORT_ID,))
    cur.execute("DELETE FROM report_indicator WHERE report_id=%s", (REPORT_ID,))
    cur.execute("UPDATE report_info SET conclusion_text=NULL WHERE id=%s", (REPORT_ID,))
    cur.execute("UPDATE report_task SET status='queued', error_message=NULL, "
                "completed_at=NULL WHERE id=%s", (TASK_ID,))
    c.commit()


def wait_task(c, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        cur = c.cursor()
        cur.execute("SELECT status, error_message FROM report_task WHERE id=%s", (TASK_ID,))
        row = cur.fetchone()
        if row and row[0] in ("completed", "failed"):
            return {"status": row[0], "error": (row[1] or "")[:200],
                    "elapsed_s": round(time.time() - t0)}
        time.sleep(5)
    return {"status": "TIMEOUT", "error": "", "elapsed_s": round(time.time() - t0)}


def wait_interp(c, timeout=2400):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        cur = c.cursor()
        cur.execute("SELECT id, status FROM report_interpretation WHERE report_id=%s "
                    "ORDER BY id DESC LIMIT 1", (REPORT_ID,))
        row = cur.fetchone()
        if row:
            last = row[1]
            if row[1] in ("completed", "failed"):
                return {"interp_id": row[0], "status": row[1],
                        "elapsed_s": round(time.time() - t0)}
        time.sleep(10)
    return {"interp_id": None, "status": f"TIMEOUT(last={last})", "elapsed_s": round(time.time() - t0)}


def snapshot(c):
    cur = c.cursor(pymysql.cursors.DictCursor)
    out = {}
    cur.execute("SELECT conclusion_text FROM report_info WHERE id=%s", (REPORT_ID,))
    conc = (cur.fetchone() or {}).get("conclusion_text") or ""
    out["conc_len"] = len(conc)
    out["conc_has_old_table"] = "本次体检结论" in conc
    out["conc_has_warm"] = "温馨提醒" in conc
    out["conc_tail"] = conc[-80:]
    out["conc_head"] = conc[:60]
    cur.execute("SELECT id, item_name, result_value, unit, ref_range_low, ref_range_high, "
                "signal_flag FROM report_indicator WHERE report_id=%s AND item_name LIKE '%25羟%' "
                "OR (report_id=%s AND item_name LIKE '%维生素D%')", (REPORT_ID, REPORT_ID))
    out["vd_rows"] = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT id FROM report_interpretation WHERE report_id=%s AND status='completed' "
                "ORDER BY id DESC LIMIT 1", (REPORT_ID,))
    row = cur.fetchone()
    out["interp_id"] = row["id"] if row else None
    if row:
        cur.execute("""SELECT ij.item_name, ij.result_value, ij.deviation, ij.color_level,
                              ij.source
                       FROM indicator_judgment ij
                       JOIN report_indicator ri ON ri.id = ij.indicator_id
                       WHERE ij.interpretation_id=%s ORDER BY ij.id""", (row["id"],))
        rows = cur.fetchall()
        out["conclusion_items"] = [
            {"name": (r["item_name"] or "").replace(" ", "").replace("\u3000", ""),
             "color": r["color_level"]}
            for r in rows if r["source"] == "conclusion"
        ]
        out["n_indicator"] = sum(1 for r in rows if r["source"] != "conclusion")
    else:
        out["conclusion_items"] = []
        out["n_indicator"] = 0
    return out


def main():
    c = conn()
    try:
        print("[1] clean old rows", flush=True)
        clean(c)
        print("[2] publish parsing", flush=True)
        rabbitmq.publish(TaskMessage(
            task_type="parsing", hospital_id=HOSPITAL_ID, priority="normal",
            payload={"task_id": TASK_ID, "hospital_id": HOSPITAL_ID},
        ))
        print("[3] wait parsing:", wait_task(c), flush=True)
        print("[4] publish interpretation", flush=True)
        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=HOSPITAL_ID, priority="normal",
            payload={"report_id": REPORT_ID, "hospital_id": HOSPITAL_ID},
        ))
        print("[5] wait interp:", wait_interp(c), flush=True)
        snap = snapshot(c)
        print("[6] SNAPSHOT:", flush=True)
        for k, v in snap.items():
            print(f"    {k}: {v}", flush=True)
    finally:
        c.close()


if __name__ == "__main__":
    main()
