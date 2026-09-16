"""生成报告级三件套基线(2026-09-12, 口径修正版)。

**基线 = 用户验收过的 DB 结果快照**(不再用离线链产物):
  ① 指标黄红名单(最新 completed interpretation 的 source=indicator)
  ② conclusion_text(归一化哈希)
  ③ 总检异常条目名单(source=conclusion)
测试直接读 DB 当前产物与基线比对 → 报告重跑后若与验收态不同即红(人工审)。

用法(backend 目录):
    .venv/bin/python scripts/gen_h003_baseline.py
"""
import hashlib
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pymysql

DB = "hospital_H003"
REPORT_IDS = [1, 2, 3, 4, 5, 6, 7, 20, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31]  # 2026-09-16: +第三批 27-31
OUT = BACKEND / "tests" / "modules" / "report" / "baselines" / "h003_baseline.json"


def norm(n: str) -> str:
    return (n or "").replace(" ", "").replace("\u3000", "")


def norm_text(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def main():
    c = pymysql.connect(host="127.0.0.1", user="root", password="root",
                        db=DB, charset="utf8mb4")
    cur = c.cursor()
    out = {}
    for rid in REPORT_IDS:
        cur.execute("SELECT name, unit_name, conclusion_text FROM report_info WHERE id=%s", (rid,))
        row = cur.fetchone()
        if not row:
            print(f"[{rid}] skip: no report")
            continue
        name, unit, conc = row[0], row[1], row[2] or ""
        cur.execute("SELECT id FROM report_interpretation WHERE report_id=%s "
                    "AND status='completed' ORDER BY id DESC LIMIT 1", (rid,))
        r = cur.fetchone()
        if not r:
            print(f"[{rid}] skip: no completed interp")
            continue
        iid = r[0]
        cur.execute("""SELECT ij.item_name, ij.color_level FROM indicator_judgment ij
            WHERE ij.interpretation_id=%s AND ij.source='conclusion'""", (iid,))
        conclusions = sorted((norm(x[0]), x[1]) for x in cur.fetchall())
        cur.execute("""SELECT ij.item_name, ij.color_level FROM indicator_judgment ij
            WHERE ij.interpretation_id=%s AND ij.source='indicator'
              AND ij.color_level IN ('yellow','red')""", (iid,))
        indicators = sorted((norm(x[0]), x[1]) for x in cur.fetchall())
        out[str(rid)] = {
            "name": f"{name}({unit or ''})",
            "interp_id": iid,
            "conclusion_len": len(conc),
            "conclusion_hash": hashlib.sha1(norm_text(conc).encode("utf-8")).hexdigest()[:16],
            "indicators": indicators,
            "conclusions": conclusions,
        }
        print(f"[{rid}] {name}: 指标黄红 {len(indicators)} | 结论 {len(conclusions)} | conc {len(conc)}")
    c.close()
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved ->", OUT)


if __name__ == "__main__":
    main()
