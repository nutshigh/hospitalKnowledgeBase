#!/usr/bin/env python3
"""007_fix_indicator_standard.py:按修复后 term_normalizer 词表重算 report_indicator.item_name_standard。

背景:旧词表子串匹配把血常规子项(血小板比积/平均体积/分布宽度/大血小板比率、红细胞压积/
MCV/MCH/MCHC/RDW、小而密LDL 等)及尿检/酸碱度项误并入父项标准名。本脚本逐 DISTINCT
item_name 用新词表重算并回填。

默认只处理当前解析管线产出的库;不动 hospital_H003 / hospital_H004。

用法(在 backend 目录):
    .venv/bin/python scripts/manual_migrations/007_fix_indicator_standard.py             # dry-run
    .venv/bin/python scripts/manual_migrations/007_fix_indicator_standard.py --apply      # 落库
    .venv/bin/python scripts/manual_migrations/007_fix_indicator_standard.py --db hospital_1 --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text  # noqa: E402

from app.core.database import get_session  # noqa: E402
from app.core.term_normalizer import normalize_item_name  # noqa: E402

DEFAULT_DBS = ["hospital_1", "hospital_H001", "hospital_H002"]


def plan_updates(db, db_name: str):
    """返回 [(item_name, old_standard, new_standard, rows)];按 DISTINCT item_name 汇总待改行数。"""
    rows = db.execute(text(
        "SELECT DISTINCT item_name, item_name_standard FROM report_indicator "
        "WHERE item_name IS NOT NULL AND item_name <> ''"
    )).fetchall()
    changes = []
    for item_name, old in rows:
        new, _ = normalize_item_name(item_name)
        if new == old:
            continue
        cnt = db.execute(text(
            "SELECT COUNT(*) FROM report_indicator WHERE item_name = :n AND "
            "(item_name_standard IS NULL OR item_name_standard <> :new)"
        ), {"n": item_name, "new": new}).scalar()
        if cnt:
            changes.append((item_name, old, new, int(cnt)))
    return changes


def apply(db, changes) -> None:
    for item_name, _old, new, _cnt in changes:
        db.execute(text(
            "UPDATE report_indicator SET item_name_standard = :new WHERE item_name = :n"
        ), {"n": item_name, "new": new})
    db.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description="按新词表重算 report_indicator.item_name_standard")
    ap.add_argument("--db", action="append", dest="dbs", default=[],
                    help="目标库名(可多次);默认 %s" % ", ".join(DEFAULT_DBS))
    ap.add_argument("--apply", action="store_true", help="实际写库;缺省为 dry-run 只打印")
    args = ap.parse_args()
    dbs = args.dbs or DEFAULT_DBS

    total = 0
    for db_name in dbs:
        db = get_session(db_name)
        try:
            changes = plan_updates(db, db_name)
            rows = sum(c[3] for c in changes)
            total += rows
            print("== %s: %d distinct item_name, %d rows to change" % (db_name, len(changes), rows))
            for item_name, old, new, cnt in sorted(changes, key=lambda c: -c[3])[:80]:
                print("   %-26s %-30s -> %-30s (%d rows)"
                      % (item_name, old or "(NULL)", new, cnt))
            if len(changes) > 80:
                print("   ... 其余 %d 项略" % (len(changes) - 80))
            if args.apply:
                apply(db, changes)
                print("   applied.")
        finally:
            db.close()
    print("TOTAL rows: %d (mode: %s)" % (total, "APPLY" if args.apply else "dry-run"))


if __name__ == "__main__":
    main()
