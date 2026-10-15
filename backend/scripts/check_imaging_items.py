"""影像 11 项离线审表(2026-10-14): 对 H003/H004 基线报告跑 build_imaging_items, 只读。

用法(backend 目录):
    .venv/bin/python scripts/check_imaging_items.py
输出:
    /tmp/imaging_items_review.txt  —— 每份报告 报告×11项 等级 + 非绿项命中证据([条目]/[原文])
    stdout 同内容
spec: docs/superpowers/specs/2026-10-14-imaging-items-module-design.md
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pymysql

from app.modules.report.imaging_items import build_imaging_items, collect_evidence

OUT = Path("/tmp/imaging_items_review.txt")
CASES = [
    ("hospital_H003", BACKEND / "tests/modules/report/baselines/h003_baseline.json"),
    ("hospital_H004", BACKEND / "tests/modules/report/baselines/h004_baseline.json"),
]
EVIDENCE_PER_ITEM = 4


def main():
    conn = pymysql.connect(host="127.0.0.1", user="root", password="root",
                           charset="utf8mb4")
    cur = conn.cursor()
    out = []
    stats = {"reports": 0, "with_yellow": 0, "with_red": 0, "all_green": 0}
    for db, base in CASES:
        ids = sorted(int(k) for k in json.loads(base.read_text(encoding="utf-8")))
        for rid in ids:
            cur.execute(
                f"SELECT name, parsed_name, gender, conclusion_text FROM {db}.report_info WHERE id=%s",
                (rid,))
            row = cur.fetchone()
            if not row:
                out.append(f"{db[-4:]}-{rid} 缺失")
                continue
            disp = row[1] or row[0]
            name, gender, text = disp, row[2], row[3] or ""
            cur.execute(
                f"SELECT id FROM {db}.report_interpretation WHERE report_id=%s "
                f"AND status='completed' ORDER BY id DESC LIMIT 1", (rid,))
            r = cur.fetchone()
            entries = []
            if r:
                cur.execute(
                    f"SELECT item_name, color_level FROM {db}.indicator_judgment "
                    f"WHERE interpretation_id=%s AND source='conclusion'", (r[0],))
                entries = list(cur.fetchall())
            items = build_imaging_items(text, gender, entries)
            evidence = collect_evidence(text, entries)
            stats["reports"] += 1
            levels = {i["name"]: i["level"] for i in items}
            if levels and all(v == "green" for v in levels.values()):
                stats["all_green"] += 1
            if "red" in levels.values():
                stats["with_red"] += 1
            if "yellow" in levels.values():
                stats["with_yellow"] += 1
            summary = " ".join(f"{n}={v}" for n, v in levels.items() if v != "green") or "(全绿)"
            if not levels:
                summary = "(无结论文本且无条目 → [])"
            out.append(f"{name}({gender}) {db[-4:]}-{rid:>2}: {summary}")
            for item in items:
                if item["level"] == "green":
                    continue
                out.append(f"      [{item['level']}] {item['name']}")
                for ev in evidence.get(item["name"], [])[:EVIDENCE_PER_ITEM]:
                    out.append(f"          {ev}")
    conn.close()
    out.append("")
    out.append(f"统计: {stats}")
    text_out = "\n".join(out)
    OUT.write_text(text_out, encoding="utf-8")
    print(text_out)
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()
