from __future__ import annotations

from sqlalchemy import text

from app.modules.interpretation.models import IndicatorJudgment, ReportInterpretation
from app.modules.report.models import ReportIndicator, ReportInfo
from app.modules.risk.models import DiseaseHit
from app.modules.risk.worker import compute_and_store
from app.modules.szbf.transformer import TargetReport

SYNC_MAP_DDL_SQLITE = text("""
CREATE TABLE IF NOT EXISTS szbf_sync_map (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_user_id VARCHAR(32) NOT NULL,
  exam_code VARCHAR(64) NOT NULL,
  report_id INTEGER NOT NULL,
  source_updated_at VARCHAR(32),
  raw_conclusion TEXT,
  raw_con_suggest TEXT,
  synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (source_user_id, exam_code)
)
""")

SYNC_STATE_DDL_SQLITE = text("""
CREATE TABLE IF NOT EXISTS szbf_sync_state (
  source_key VARCHAR(32) PRIMARY KEY,
  last_detail_update VARCHAR(32),
  last_conclusion_update VARCHAR(32),
  last_run_at DATETIME,
  status VARCHAR(16),
  message TEXT
)
""")


def _clip(value: str | None, length: int) -> str | None:
    if value is None:
        return None
    return value[:length]


def find_report_id(db, source_user_id: str, exam_code: str) -> int | None:
    row = db.execute(text(
        "SELECT report_id FROM szbf_sync_map "
        "WHERE source_user_id=:u AND exam_code=:e"
    ), {"u": source_user_id, "e": exam_code}).first()
    return int(row[0]) if row else None


def _delete_children(db, report_id: int) -> None:
    db.query(DiseaseHit).filter(DiseaseHit.report_id == report_id).delete()
    interp_ids = [
        r[0] for r in db.execute(text(
            "SELECT id FROM report_interpretation WHERE report_id=:r"
        ), {"r": report_id})
    ]
    if interp_ids:
        db.query(IndicatorJudgment).filter(
            IndicatorJudgment.interpretation_id.in_(interp_ids)
        ).delete(synchronize_session=False)
    db.query(ReportInterpretation).filter(
        ReportInterpretation.report_id == report_id
    ).delete(synchronize_session=False)
    db.query(ReportIndicator).filter(
        ReportIndicator.report_id == report_id
    ).delete(synchronize_session=False)


def _delete_map(db, report_id: int) -> None:
    db.execute(text("DELETE FROM szbf_sync_map WHERE report_id=:r"), {"r": report_id})


def delete_report(db, report_id: int) -> None:
    _delete_children(db, report_id)
    _delete_map(db, report_id)
    db.query(ReportInfo).filter(ReportInfo.id == report_id).delete()
    db.commit()


def delete_all_szbf(db) -> int:
    rows = db.execute(text(
        "SELECT id FROM report_info WHERE check_type='SZBF'"
    )).fetchall()
    ids = [int(r[0]) for r in rows]
    for report_id in ids:
        _delete_children(db, report_id)
        _delete_map(db, report_id)
        db.query(ReportInfo).filter(ReportInfo.id == report_id).delete()
    # 报告可能已被 API/手工删除, 映射行会残留成孤儿; 全量重建时一并清理,
    # 让后续 upsert/增量从干净状态开始。
    db.execute(text("DELETE FROM szbf_sync_map"))
    db.execute(text("DELETE FROM szbf_sync_state"))
    db.commit()
    return len(ids)


def upsert_report(db, target: TargetReport) -> int:
    report_id = find_report_id(db, target.source_user_id, target.exam_code)
    if report_id:
        report = db.get(ReportInfo, report_id)
        if report is None:
            # 映射指向已删除报告(API/手工删): 清理孤儿子表与映射后按新报告写入。
            _delete_children(db, report_id)
            _delete_map(db, report_id)
            report_id = None
    if report_id:
        _delete_children(db, report_id)
        report.name = _clip(target.name, 50)
        report.parsed_name = _clip(target.name, 50)
        report.gender = target.gender
        report.age = target.age
        report.report_date = target.report_date
        report.check_type = "SZBF"
        report.unit_name = _clip(target.unit_name, 100)
        report.conclusion_text = target.conclusion_text
        db.execute(text(
            "UPDATE szbf_sync_map SET raw_conclusion=:rc, raw_con_suggest=:rs, "
            "synced_at=CURRENT_TIMESTAMP WHERE report_id=:r"
        ), {"rc": target.raw_conclusion, "rs": target.raw_con_suggest, "r": report_id})
    else:
        report = ReportInfo(
            task_id=None,
            user_id=target.user_id,
            name=_clip(target.name, 50),
            parsed_name=_clip(target.name, 50),
            gender=target.gender,
            age=target.age,
            report_date=target.report_date,
            check_type="SZBF",
            unit_name=_clip(target.unit_name, 100),
            conclusion_text=target.conclusion_text,
        )
        db.add(report)
        db.flush()
        report_id = report.id
        db.execute(text(
            "INSERT INTO szbf_sync_map "
            "(source_user_id, exam_code, report_id, raw_conclusion, raw_con_suggest) "
            "VALUES (:u, :e, :r, :rc, :rs)"
        ), {"u": target.source_user_id, "e": target.exam_code, "r": report_id,
            "rc": target.raw_conclusion, "rs": target.raw_con_suggest})

    indicator_rows = []
    for item in target.indicators:
        row = ReportIndicator(
            report_id=report_id,
            item_name=_clip(item.item_name, 100),
            item_name_standard=_clip(item.item_name_standard, 100),
            item_code=_clip(item.item_code, 50),
            result_value=_clip(item.result_value, 50),
            unit=_clip(item.unit, 20),
            ref_range_low=_clip(item.ref_range_low, 50),
            ref_range_high=_clip(item.ref_range_high, 50),
            category=_clip(item.category, 50),
            raw_text=item.raw_text,
            signal_flag=1,
        )
        db.add(row)
        indicator_rows.append((row, item))
    db.flush()

    interpretation = ReportInterpretation(
        report_id=report_id,
        overall_level="yellow" if indicator_rows else "green",
        red_count=0,
        yellow_count=len(indicator_rows),
        green_count=0,
        status="completed",
    )
    db.add(interpretation)
    db.flush()

    for row, item in indicator_rows:
        db.add(IndicatorJudgment(
            interpretation_id=interpretation.id,
            indicator_id=row.id,
            item_name=_clip(item.item_name_standard or item.item_name, 100),
            result_value=_clip(item.result_value, 50),
            deviation=_clip(item.deviation, 10),
            color_level="yellow",
            source=item.source,
            certainty="high" if item.source == "indicator" else "medium",
        ))
    db.flush()

    compute_and_store(db, report_id, interpretation.id)

    for disease in target.conclusion_diseases:
        disease_name = _clip(disease.name, 100)
        exists = db.query(DiseaseHit).filter(
            DiseaseHit.report_id == report_id,
            DiseaseHit.disease_name == disease_name,
        ).first()
        if exists:
            continue
        db.add(DiseaseHit(
            report_id=report_id,
            interpretation_id=interpretation.id,
            user_id=target.user_id,
            unit_name=_clip(target.unit_name, 100),
            report_date=target.report_date,
            disease_name=disease_name,
            disease_category=disease.category,
            disease_class=_clip(disease.klass, 100),
            hit_type="single",
            hit_items=[disease.name],
        ))
    db.commit()
    return report_id


def read_state(db, source_key: str) -> dict:
    row = db.execute(text(
        "SELECT last_detail_update, last_conclusion_update, last_run_at, status, message "
        "FROM szbf_sync_state WHERE source_key=:k"
    ), {"k": source_key}).first()
    if not row:
        return {}
    return {
        "last_detail_update": row[0],
        "last_conclusion_update": row[1],
        "last_run_at": row[2],
        "status": row[3],
        "message": row[4],
    }


def write_state(db, source_key: str, **kwargs) -> None:
    existing = db.execute(text(
        "SELECT 1 FROM szbf_sync_state WHERE source_key=:k"
    ), {"k": source_key}).first()
    if existing:
        db.execute(text(
            "UPDATE szbf_sync_state SET last_detail_update=:d, "
            "last_conclusion_update=:c, last_run_at=CURRENT_TIMESTAMP, "
            "status=:s, message=:m WHERE source_key=:k"
        ), {"d": kwargs.get("last_detail_update"), "c": kwargs.get("last_conclusion_update"),
            "s": kwargs.get("status"), "m": kwargs.get("message"), "k": source_key})
    else:
        db.execute(text(
            "INSERT INTO szbf_sync_state "
            "(source_key, last_detail_update, last_conclusion_update, "
            "last_run_at, status, message) "
            "VALUES (:k, :d, :c, CURRENT_TIMESTAMP, :s, :m)"
        ), {"k": source_key, "d": kwargs.get("last_detail_update"),
            "c": kwargs.get("last_conclusion_update"),
            "s": kwargs.get("status"), "m": kwargs.get("message")})
    db.commit()
