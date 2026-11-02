from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterable


def safe_filename(value: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", str(value or "").strip())
    return name or "unknown"


def build_conclusion_only_markdown(
    station_name: str,
    org_id: str,
    rows: Iterable[tuple[str, str]],
) -> str:
    entries = sorted(rows, key=lambda row: (row[1] or "", str(row[0])))
    lines = [
        f"# {station_name}：仅有结论、无明细用户",
        "",
        f"生成时间：{date.today().isoformat()}",
        "",
        "数据源：`szbf_exam_conclusion_record` / `szbf_exam_detail` / `ba_user_basic` / `ba_user`。",
        "",
        "口径：在结论表中有记录，且 `szbf_exam_detail` 中没有任何明细行的用户。",
        "",
        "## 汇总",
        "",
        "| 总站 | org_id | 仅有结论、无明细用户数 |",
        "|---|---:|---:|",
        f"| {station_name} | {org_id} | {len(entries)} |",
        "",
        "## 用户明细",
        "",
        f"共 {len(entries)} 人。",
        "",
        "| 序号 | 姓名 | user_id |",
        "|---:|---|---|",
    ]
    for index, (user_id, name) in enumerate(entries, 1):
        safe_name = str(name or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {index} | {safe_name} | {user_id} |")
    lines.append("")
    return "\n".join(lines)


def write_conclusion_only_report(
    station_name: str,
    org_id: str,
    rows: Iterable[tuple[str, str]],
    output_dir: str | Path,
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{safe_filename(station_name)}_仅有结论无明细用户.md"
    path.write_text(
        build_conclusion_only_markdown(station_name, org_id, rows),
        encoding="utf-8",
    )
    return path
