#!/usr/bin/env python
"""总站采集数据同步：szbf_exam_detail/conclusion -> hospital_<id>。"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.core.database import get_session
from app.core.logging_config import setup_logging
from app.modules.szbf.conclusion_report import write_conclusion_only_report
from app.modules.szbf.profiles import get_profile
from app.modules.szbf.source import SourceReader
from app.modules.szbf.transformer import transform_exam
from app.modules.szbf.writer import (
    delete_all_szbf,
    read_state,
    upsert_report,
    write_state,
)

import logging

log = logging.getLogger("app.szbf.sync")

_DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[2] / "docs" / "output"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="同步 szbf 采集数据到后端租户库")
    parser.add_argument("--org-id", default=settings.SZBF_ORG_ID)
    parser.add_argument("--target-hospital", default=settings.SZBF_TARGET_HOSPITAL_ID)
    parser.add_argument("--source-host", default=settings.SZBF_SOURCE_HOST)
    parser.add_argument("--source-port", type=int, default=settings.SZBF_SOURCE_PORT)
    parser.add_argument("--source-user", default=settings.SZBF_SOURCE_USER)
    parser.add_argument("--source-password", default=settings.SZBF_SOURCE_PASSWORD)
    parser.add_argument("--source-db", default=settings.SZBF_SOURCE_DB)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full-rebuild", action="store_true")
    mode.add_argument("--incremental", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--conclusion-report-dir",
        default=str(_DEFAULT_REPORT_DIR),
        help="全量导入后输出仅结论无明细用户 md 的目录",
    )
    return parser


def _source_engine(args):
    url = (
        f"mysql+pymysql://{quote_plus(args.source_user)}:{quote_plus(args.source_password)}"
        f"@{args.source_host}:{args.source_port}/{args.source_db}?charset=utf8mb4"
    )
    return create_engine(url, pool_pre_ping=True)


def _warn_if_no_disease_seed(db) -> None:
    """disease_hit 依赖租户库的中央词典/规则; 缺失时只告警, 不阻断同步。"""
    try:
        mappings = db.execute(text(
            "SELECT COUNT(*) FROM disease_mapping WHERE enabled=1"
        )).scalar() or 0
        rules = db.execute(text(
            "SELECT COUNT(*) FROM disease_rule WHERE enabled=1"
        )).scalar() or 0
    except Exception:  # noqa: BLE001
        log.warning("无法检查 disease_mapping/disease_rule, 跳过预检", exc_info=True)
        return
    if mappings == 0 and rules == 0:
        log.warning(
            "disease_mapping/disease_rule 均无启用数据, 本次同步的 disease_hit 将为空; "
            "请先在目标租户库执行 risk.seed.sync_central"
        )


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    if args.limit < 0:
        log.error("--limit 不能为负数: %s", args.limit)
        return 2
    if args.limit and not args.dry_run:
        log.error("--limit 仅可与 --dry-run 一起使用，避免不完整导入推进水位")
        return 2
    source_key = f"szbf:{args.org_id}->{args.target_hospital}"
    if not args.source_host or not args.source_db:
        log.error("缺少 source 配置：--source-host/--source-db 或 SZBF_SOURCE_*")
        return 2
    engine = _source_engine(args)
    reader = SourceReader(engine, args.org_id)
    db = get_session(f"hospital_{args.target_hospital}")
    profile = get_profile(args.org_id)
    started = time.time()
    previous_state: dict = {}
    deleted = False
    total_exams = total_indicators = total_diseases = 0
    try:
        _warn_if_no_disease_seed(db)
        previous_state = read_state(db, source_key)
        # 水位以"开始读取源库"的时刻为准: 处理耗时不会把窗口推后造成漏更新。
        fetch_started_at = datetime.now()
        if args.full_rebuild:
            exams = reader.fetch_exams(limit=args.limit or None)
        else:
            changed = previous_state.get("last_detail_update") or "1970-01-01 00:00:00"
            exams = reader.fetch_exams(changed_since=changed, limit=args.limit or None)
            conclusion_changed = previous_state.get("last_conclusion_update") or "1970-01-01 00:00:00"
            seen = {(e.user_id, e.exam_code) for e in exams}
            for uid in reader.fetch_changed_conclusion_users(conclusion_changed):
                exam = reader.fetch_latest_exam_for_user(uid)
                if exam and (exam.user_id, exam.exam_code) not in seen:
                    seen.add((exam.user_id, exam.exam_code))
                    exams.append(exam)
        # 先完成全部转换再删旧数据: 源读取/转换失败时不会破坏现有报告。
        targets = []
        for exam in exams:
            target = transform_exam(exam, profile, settings.SZBF_INDICATOR_SOURCE)
            targets.append(target)
            total_exams += 1
            total_indicators += len(target.indicators)
            total_diseases += len(target.conclusion_diseases)
        if args.full_rebuild and not args.dry_run:
            deleted_count = delete_all_szbf(db)
            deleted = True
            log.info("清空旧 SZBF 报告 %s 条", deleted_count)
        if not args.dry_run:
            batch_size = settings.SZBF_SYNC_BATCH_SIZE
            for index, target in enumerate(targets, start=1):
                upsert_report(db, target)
                if batch_size > 0 and index % batch_size == 0:
                    log.info("已同步 %s 场", index)
            watermark = fetch_started_at - timedelta(
                minutes=settings.SZBF_SYNC_LOOKBACK_MINUTES
            )
            watermark_text = watermark.strftime("%Y-%m-%d %H:%M:%S")
            write_state(
                db, source_key,
                last_detail_update=watermark_text,
                last_conclusion_update=watermark_text,
                status="ok",
                message=f"exams={total_exams} indicators={total_indicators} diseases={total_diseases}",
            )
            if args.full_rebuild:
                try:
                    report_rows = reader.fetch_conclusion_only_users()
                    report_path = write_conclusion_only_report(
                        reader.station_name(),
                        args.org_id,
                        report_rows,
                        args.conclusion_report_dir,
                    )
                    log.info(
                        "仅结论无明细用户已输出: %s (%d 人)",
                        report_path, len(report_rows),
                    )
                except Exception:  # noqa: BLE001
                    log.exception("生成仅结论无明细用户失败(不影响同步)")
        log.info(
            "完成 source=%s target=%s exams=%s indicators=%s diseases=%s elapsed=%.1fs",
            args.org_id, args.target_hospital, total_exams, total_indicators,
            total_diseases, time.time() - started,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("同步失败: %s", exc)
        rollback = getattr(db, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception:  # noqa: BLE001
                log.warning("回滚失败", exc_info=True)
        if not args.dry_run:
            if deleted:
                # 旧数据已删除且本次写入失败: 旧水位不可再用, 必须强制下次全量重拉。
                write_state(
                    db, source_key,
                    last_detail_update=None,
                    last_conclusion_update=None,
                    status="failed",
                    message=(
                        "full refetch required after delete: "
                        f"{str(exc)[:900]}"
                    ),
                )
            else:
                write_state(
                    db, source_key,
                    last_detail_update=previous_state.get("last_detail_update"),
                    last_conclusion_update=previous_state.get("last_conclusion_update"),
                    status="failed", message=str(exc)[:1000],
                )
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
