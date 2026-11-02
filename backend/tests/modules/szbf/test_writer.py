from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.interpretation import models as interp_models  # noqa: F401
from app.modules.report import models as report_models  # noqa: F401
from app.modules.risk import models as risk_models  # noqa: F401
from app.modules.szbf.conclusion_diseases import ConclusionDisease
from app.modules.szbf.transformer import TargetIndicator, TargetReport
from app.modules.szbf import writer


def _count(db, table):
    return db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    session.execute(writer.SYNC_MAP_DDL_SQLITE)
    session.execute(writer.SYNC_STATE_DDL_SQLITE)
    session.commit()
    monkeypatch.setattr(writer, "compute_and_store", lambda *a, **k: 0)
    return session


def _target():
    return TargetReport(
        source_user_id="u1", exam_code="e1", user_id="z" + "a" * 15,
        name="张三", gender="男", age=30, report_date=date(2026, 10, 13),
        unit_name="北京总站", conclusion_text="1.脂肪肝",
        raw_conclusion=None, raw_con_suggest="1.脂肪肝",
        indicators=[TargetIndicator(
            item_name="总胆固醇(CHOL)", item_name_standard="总胆固醇(CHOL)",
            item_code="CHOL", result_value="5.32", unit="mmol/L",
            ref_range_low=None, ref_range_high="5.18", category="检验科",
            raw_text="总胆固醇(CHOL)", source="indicator", deviation="偏高",
        )],
        conclusion_diseases=[],
    )


def test_upsert_is_idempotent(db):
    rid1 = writer.upsert_report(db, _target())
    rid2 = writer.upsert_report(db, _target())
    assert rid1 == rid2
    assert _count(db, "report_info") == 1
    assert _count(db, "report_indicator") == 1
    assert _count(db, "indicator_judgment") == 1
    assert _count(db, "szbf_sync_map") == 1


def test_upsert_clips_overlong_strings_to_column_limits(db):
    target = TargetReport(
        source_user_id="u1", exam_code="e1", user_id="z" + "a" * 15,
        name="张" * 80, gender="男", age=30, report_date=date(2026, 10, 13),
        unit_name="医" * 150, conclusion_text="1." + "长" * 300,
        raw_conclusion=None, raw_con_suggest=None,
        indicators=[TargetIndicator(
            item_name="指" * 150, item_name_standard="标" * 150,
            item_code="C" * 80, result_value="9" * 80, unit="u" * 40,
            ref_range_low="1" * 80, ref_range_high="2" * 80,
            category="科" * 80, raw_text="r" * 300,
            source="indicator", deviation="偏" * 30,
        )],
        conclusion_diseases=[ConclusionDisease(
            name="病" * 150, category="CHRONIC", klass="类" * 150,
        )],
    )
    rid = writer.upsert_report(db, target)

    report = db.get(report_models.ReportInfo, rid)
    assert report.name == "张" * 50
    assert report.parsed_name == "张" * 50
    assert report.unit_name == "医" * 100
    assert report.conclusion_text == "1." + "长" * 300

    indicator = db.query(report_models.ReportIndicator).filter_by(
        report_id=rid
    ).one()
    assert indicator.item_name == "指" * 100
    assert indicator.item_name_standard == "标" * 100
    assert indicator.item_code == "C" * 50
    assert indicator.result_value == "9" * 50
    assert indicator.unit == "u" * 20
    assert indicator.ref_range_low == "1" * 50
    assert indicator.ref_range_high == "2" * 50
    assert indicator.category == "科" * 50
    assert indicator.raw_text == "r" * 300

    judgment = db.query(interp_models.IndicatorJudgment).filter_by(
        indicator_id=indicator.id
    ).one()
    assert judgment.item_name == "标" * 100
    assert judgment.result_value == "9" * 50
    assert judgment.deviation == "偏" * 10

    hit = db.query(risk_models.DiseaseHit).filter_by(report_id=rid).one()
    assert hit.disease_name == "病" * 100
    assert hit.disease_class == "类" * 100
    assert hit.unit_name == "医" * 100

    db.expunge_all()
    assert writer.upsert_report(db, target) == rid
    report = db.get(report_models.ReportInfo, rid)
    assert report.name == "张" * 50
    assert report.unit_name == "医" * 100


def test_delete_report(db):
    rid = writer.upsert_report(db, _target())
    writer.delete_report(db, rid)
    assert _count(db, "report_info") == 0
    assert _count(db, "szbf_sync_map") == 0


def _insert_map(db, report_id):
    db.execute(text(
        "INSERT INTO szbf_sync_map (source_user_id, exam_code, report_id) "
        "VALUES (:u, :e, :r)"
    ), {"u": "u1", "e": "e1", "r": report_id})
    db.commit()


def test_upsert_heals_orphan_map_row(db):
    _insert_map(db, 999)
    assert writer.find_report_id(db, "u1", "e1") == 999

    rid = writer.upsert_report(db, _target())

    assert rid != 999
    assert db.get(report_models.ReportInfo, rid) is not None
    assert _count(db, "report_info") == 1
    assert _count(db, "szbf_sync_map") == 1
    assert writer.find_report_id(db, "u1", "e1") == rid


def test_delete_all_clears_orphan_map_and_state(db):
    _insert_map(db, 999)
    db.execute(text(
        "INSERT INTO szbf_sync_state (source_key, status) VALUES ('k', 'ok')"
    ))
    db.commit()

    deleted = writer.delete_all_szbf(db)

    assert deleted == 0
    assert _count(db, "szbf_sync_map") == 0
    assert _count(db, "szbf_sync_state") == 0
