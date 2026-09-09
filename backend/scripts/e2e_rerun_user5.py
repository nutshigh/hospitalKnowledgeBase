"""端到端验证(2026-09-07): 用当前 worker 代码对 H003·USER5 报告重跑完整解读链路,
并与重跑前(手动修正后)的数据快照对比。

用法(在 backend 目录):
    nohup .venv/bin/python scripts/e2e_rerun_user5.py 20 22 23 24 25 > /tmp/opencode/e2e_h003.log 2>&1 &

流程(逐份): 快照基线 -> 删 interpretation+judgments(保留 raw 占位行, 复现事故最坏态)
           -> publish interpretation -> 轮询至新 interpretation 终态 -> 快照结果 -> diff。
"""
import json
import sys
import time
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.rabbitmq import TaskMessage, rabbitmq

DB = "hospital_H003"
HOSPITAL_ID = "H003"
REPORTS = [int(x) for x in sys.argv[1:]] or [20, 22, 23, 24, 25]
PER_REPORT_TIMEOUT = 3600
OUT = Path("/home/wjyy2/logs/e2e_h003_snapshot.json")


def conn():
    return pymysql.connect(host="127.0.0.1", user="root", password="root",
                           db=DB, charset="utf8mb4")


def snapshot(c, report_id: int) -> dict:
    cur = c.cursor()
    cur.execute(
        "SELECT id, status, completed_at FROM report_interpretation "
        "WHERE report_id=%s ORDER BY id DESC LIMIT 1", (report_id,))
    row = cur.fetchone()
    interp_id, status, done_at = (row if row else (None, None, None))
    cur.execute(
        """SELECT ij.item_name, ij.result_value, ij.deviation, ij.color_level, ij.source,
                  ij.certainty, ri.raw_text
           FROM indicator_judgment ij
           JOIN report_indicator ri ON ri.id = ij.indicator_id
           WHERE ij.interpretation_id=%s""", (interp_id,))
    rows = cur.fetchall()
    out = {"interp_id": interp_id, "status": status, "completed_at": str(done_at),
           "conclusion": [], "indicator": [], "other": []}
    for nm, res, dev, color, source, cert, raw in rows:
        e = {"name": (nm or "").strip(), "result": res, "deviation": dev,
             "color": color, "certainty": cert}
        if raw is not None:
            out["conclusion"].append(e)
        elif source and source != "indicator":
            out["other"].append({**e, "source": source})
        else:
            out["indicator"].append(e)
    return out


def wipe(c, report_id: int):
    cur = c.cursor()
    cur.execute(
        "DELETE FROM indicator_judgment WHERE interpretation_id IN "
        "(SELECT id FROM report_interpretation WHERE report_id=%s)", (report_id,))
    cur.execute("DELETE FROM report_interpretation WHERE report_id=%s", (report_id,))
    c.commit()


def publish(report_id: int):
    rabbitmq.publish(TaskMessage(
        task_type="interpretation", hospital_id=HOSPITAL_ID, priority="normal",
        payload={"report_id": report_id, "hospital_id": HOSPITAL_ID},
    ))


def wait_terminal(c, report_id: int, timeout: int):
    """轮询该 report 最新 interpretation 行; 首次出现即视为本轮新行(删除后重建)。"""
    t0 = time.time()
    last_status = None
    while time.time() - t0 < timeout:
        cur = c.cursor()
        cur.execute(
            "SELECT id, status FROM report_interpretation WHERE report_id=%s "
            "ORDER BY id DESC LIMIT 1", (report_id,))
        row = cur.fetchone()
        if row:
            last_status = row[1]
            if row[1] in ("completed", "failed"):
                return {"interp_id": row[0], "status": row[1],
                        "elapsed_s": round(time.time() - t0)}
        time.sleep(10)
    return {"interp_id": None, "status": f"TIMEOUT(last={last_status})",
            "elapsed_s": round(time.time() - t0)}


def norm_name(n: str) -> str:
    return n.replace(" ", "").replace("\u3000", "")


def diff_list(base, new, key_name="conclusion"):
    b = {(norm_name(e["name"]), e["color"]) for e in base[key_name]}
    n = {(norm_name(e["name"]), e["color"]) for e in new[key_name]}
    return {
        "removed": sorted([f"{nm}[{c}]" for nm, c in b - n]),
        "added": sorted([f"{nm}[{c}]" for nm, c in n - b]),
    }


def main():
    results = {}
    for report_id in REPORTS:
        c = conn()
        try:
            base = snapshot(c, report_id)
            wipe(c, report_id)
            publish(report_id)
            print(f"[{report_id}] wiped+publish ok; base conclusion={len(base['conclusion'])} "
                  f"indicator={len(base['indicator'])}", flush=True)
            term = wait_terminal(c, report_id, PER_REPORT_TIMEOUT)
            new = snapshot(c, report_id)
            d_concl = diff_list(base, new, "conclusion")
            d_ind = diff_list(base, new, "indicator")
            results[report_id] = {
                "base": base, "new": new, "terminal": term,
                "conclusion_diff": d_concl, "indicator_diff": d_ind,
            }
            print(f"[{report_id}] terminal={term}", flush=True)
            print(f"[{report_id}] 结论区 removed={d_concl['removed']} added={d_concl['added']}",
                  flush=True)
            print(f"[{report_id}] 指标区 removed={d_ind['removed']} added={d_ind['added']}",
                  flush=True)
        finally:
            c.close()
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print("saved ->", OUT, flush=True)


if __name__ == "__main__":
    main()
