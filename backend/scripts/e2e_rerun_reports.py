"""通用端到端重跑工具(2026-09-10; 替代 e2e_rerun_user5.py)。

流程(与既有端到端验证一致):
  逐份基线快照 -> 清产物 -> (reprocess: 重跑 process_task / reinterpret: 仅删解读)
  -> publish interpretation -> 全部投递后统一轮询终态 -> diff 汇总(仅旧有/仅新有/黄红变化)。

用法(在 backend 目录):
  # H004 指定报告(重跑解析+解读)
  .venv/bin/python scripts/e2e_rerun_reports.py --db hospital_H004 --reports 1,2,3,4,5,6
  # H004 USER6 全量
  .venv/bin/python scripts/e2e_rerun_reports.py --db hospital_H004 --user 11
  # 仅重解读(不动指标行, 如结论段/去重规则改动后)
  .venv/bin/python scripts/e2e_rerun_reports.py --db hospital_H003 --reports 22 --mode reinterpret

约定:
  - 需要 interpretation worker 在线; reprocess 走 OCR 时默认 OCR_BASE_URL=http://localhost:8006
    (可用环境变量覆盖)。
  - 快照/结果 JSON 落 --out(默认 /home/wjyy2/logs/e2e_rerun_<db>_<ts>.json)。
  - 本工具只读 DB + 重投消息 + 轮询, 不手动改产物(端到端纪律)。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql


def _conn(db: str):
    return pymysql.connect(host="127.0.0.1", user="root", password="root",
                           db=db, charset="utf8mb4")


def _query_tasks(db: str, user: str) -> list[int]:
    c = _conn(db)
    try:
        cur = c.cursor()
        cur.execute("SELECT id FROM report_task WHERE user_id=%s ORDER BY id", (user,))
        return [r[0] for r in cur.fetchall()]
    finally:
        c.close()


def snapshot(db: str, report_id: int) -> dict:
    c = _conn(db)
    try:
        cur = c.cursor()
        cur.execute("SELECT id, status FROM report_interpretation WHERE report_id=%s "
                    "ORDER BY id DESC LIMIT 1", (report_id,))
        row = cur.fetchone()
        inds, yellow = [], []
        if row:
            iid = row[0]
            cur.execute(
                """SELECT ri.item_name, ri.result_value, ri.ref_range_low,
                          ri.ref_range_high, ri.signal_flag, ij.color_level
                   FROM report_indicator ri
                   LEFT JOIN indicator_judgment ij
                     ON ij.indicator_id = ri.id AND ij.interpretation_id=%s
                   WHERE ri.report_id=%s AND ri.raw_text IS NULL""", (iid, report_id))
            for nm, rv, lo, hi, fl, cl in cur.fetchall():
                inds.append({"n": nm or "", "r": str(rv or ""),
                             "lo": lo, "hi": hi, "f": fl or 0})
                if cl in ("yellow", "red"):
                    yellow.append((nm or "", str(rv or "")))
        return {"interp": row[0] if row else None, "inds": inds,
                "yellow": sorted(set(yellow))}
    finally:
        c.close()


def wipe(db: str, report_id: int, mode: str):
    c = _conn(db)
    try:
        cur = c.cursor()
        cur.execute(
            f"DELETE FROM {db}.indicator_judgment WHERE interpretation_id IN "
            f"(SELECT id FROM {db}.report_interpretation WHERE report_id=%s)", (report_id,))
        cur.execute(f"DELETE FROM {db}.report_interpretation WHERE report_id=%s", (report_id,))
        if mode == "reprocess":
            cur.execute(f"DELETE FROM {db}.report_indicator WHERE report_id=%s", (report_id,))
        c.commit()
    finally:
        c.close()


def publish(hospital_id: str, report_id: int):
    from app.core.rabbitmq import TaskMessage, rabbitmq
    rabbitmq.publish(TaskMessage(task_type="interpretation", hospital_id=hospital_id,
                                 priority="normal",
                                 payload={"report_id": report_id, "hospital_id": hospital_id}))


def wait_all(db: str, report_ids: list[int], timeout: int) -> dict:
    t0 = time.time()
    status = {}
    while time.time() - t0 < timeout:
        c = _conn(db)
        try:
            cur = c.cursor()
            done = True
            for rid in report_ids:
                cur.execute("SELECT id, status FROM report_interpretation "
                            "WHERE report_id=%s ORDER BY id DESC LIMIT 1", (rid,))
                row = cur.fetchone()
                status[rid] = row[1] if row else "none"
                if status[rid] not in ("completed", "failed"):
                    done = False
            if done:
                break
        finally:
            c.close()
        time.sleep(10)
    return status


def diff(base: dict, new: dict) -> dict:
    def norm(s):
        return (s or "").replace(" ", "").replace("\u3000", "")
    b = {(norm(i["n"]), i["r"]) for i in base["inds"]}
    n = {(norm(i["n"]), i["r"]) for i in new["inds"]}
    only_base = sorted(f"{a}|{b_}" for a, b_ in b - n)
    only_new = sorted(f"{a}|{b_}" for a, b_ in n - b)
    y_removed = sorted(x for x in base["yellow"] if x not in new["yellow"])
    y_added = sorted(x for x in new["yellow"] if x not in base["yellow"])
    return {"only_base": only_base, "only_new": only_new,
            "yellow_removed": [f"{a}|{b_}" for a, b_ in y_removed],
            "yellow_added": [f"{a}|{b_}" for a, b_ in y_added]}


def main():
    ap = argparse.ArgumentParser(description="通用端到端重跑工具")
    ap.add_argument("--db", default="hospital_H004", help="租户库(默认 hospital_H004)")
    ap.add_argument("--hospital", default=None, help="医院 ID(默认取 db 后缀)")
    ap.add_argument("--reports", default=None, help="报告/任务 ID, 逗号分隔")
    ap.add_argument("--user", default=None, help="按 user_id 取该用户全部报告")
    ap.add_argument("--mode", choices=["reprocess", "reinterpret"], default="reprocess")
    ap.add_argument("--timeout", type=int, default=3600, help="全部报告等待上限(秒)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not args.reports and not args.user:
        ap.error("需给 --reports 或 --user")
    if args.user:
        report_ids = _query_tasks(args.db, args.user)
    else:
        report_ids = [int(x) for x in args.reports.split(",") if x.strip()]
    hospital_id = args.hospital or args.db.replace("hospital_", "")
    out_path = Path(args.out or
                    f"/home/wjyy2/logs/e2e_rerun_{args.db}_{datetime.now():%m%d_%H%M}.json")

    os.environ.setdefault("OCR_BASE_URL", "http://localhost:8006")
    if args.mode == "reprocess":
        # 说明: process 走 OCR 需 8006; 若环境另有部署请覆盖 OCR_BASE_URL。
        pass

    print(f"== 端到端重跑: db={args.db} hospital={hospital_id} mode={args.mode}")
    print(f"   reports={report_ids}")

    bases = {rid: snapshot(args.db, rid) for rid in report_ids}
    for rid in report_ids:
        wipe(args.db, rid, args.mode)
        if args.mode == "reprocess":
            from app.core.database import get_session
            from app.modules.report.service import process_task
            s = get_session(args.db)
            try:
                t0 = time.time()
                process_task(s, rid, hospital_id)
                s.commit()
                print(f"  [{rid}] process ok ({time.time()-t0:.0f}s)", flush=True)
            finally:
                s.close()
        publish(hospital_id, rid)
        print(f"  [{rid}] published", flush=True)

    status = wait_all(args.db, report_ids, args.timeout)
    results = {}
    for rid in report_ids:
        new = snapshot(args.db, rid)
        d = diff(bases[rid], new)
        results[rid] = {"status": status.get(rid), "diff": d,
                        "old_count": len(bases[rid]["inds"]),
                        "new_count": len(new["inds"])}
        print(f"  [{rid}] {status.get(rid)} 指标 {len(bases[rid]['inds'])}->{len(new['inds'])} "
              f"| 仅旧有 {len(d['only_base'])} 仅新有 {len(d['only_new'])} "
              f"| 黄红 -{len(d['yellow_removed'])} +{len(d['yellow_added'])}", flush=True)
        for k, v in d.items():
            if v:
                print(f"      {k}: {v[:6]}", flush=True)

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print("saved ->", out_path)


if __name__ == "__main__":
    main()
