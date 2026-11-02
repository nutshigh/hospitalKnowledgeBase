from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(element, compiler, **kw):
    return compiler.visit_INTEGER(element, **kw)

from app.core.dependencies import CurrentUser, get_current_user
from app.models.base import Base
from app.modules.chat import models as chat_models  # noqa: F401
from app.modules.followup import models as followup_models  # noqa: F401
from app.modules.interpretation import models as interp_models
from app.modules.interpretation.models import IndicatorJudgment, ReportInterpretation
from app.modules.interpretation.router import router as interp_router
from app.modules.report import models as report_models
from app.modules.report.models import ReportIndicator, ReportInfo
from app.modules.report.router import router as report_router
from app.modules.risk import models as risk_models  # noqa: F401


def _seed(db, report_id, check_type):
    db.add(ReportInfo(
        id=report_id, user_id="u1", name="张三", parsed_name="张三",
        check_type=check_type, report_date=None,
    ))
    db.add(ReportIndicator(
        id=report_id, report_id=report_id, item_name="总胆固醇",
        result_value="4.0", signal_flag=0,
    ))
    db.add(ReportInterpretation(
        id=report_id, report_id=report_id, overall_level="green",
        red_count=0, yellow_count=0, green_count=0, status="completed",
    ))
    db.add(IndicatorJudgment(
        interpretation_id=report_id, indicator_id=report_id,
        item_name="总胆固醇", result_value="4.0", color_level="green",
        source="indicator",
    ))
    db.commit()


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def fake_get_hospital_db(hospital_id):
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(report_router, prefix="/api/v1/reports")
    app.include_router(interp_router, prefix="/api/v1/interpretations")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="admin", hospital_id="H001"
    )
    patches = [
        patch("app.modules.report.router.get_hospital_db", fake_get_hospital_db),
        patch("app.modules.interpretation.router.get_hospital_db", fake_get_hospital_db),
    ]
    for p in patches:
        p.start()
    try:
        yield TestClient(app), Session
    finally:
        for p in patches:
            p.stop()
        app.dependency_overrides.clear()


def test_szbf_report_detail_and_delete_hidden(env):
    client, Session = env
    with Session() as db:
        _seed(db, 1, "SZBF")

    detail = client.get("/api/v1/reports/1")
    delete = client.delete("/api/v1/reports/1")

    assert detail.status_code == 404
    assert delete.status_code == 404
    with Session() as db:
        assert db.get(ReportInfo, 1) is not None


def test_normal_report_detail_and_delete_still_work(env):
    client, Session = env
    with Session() as db:
        _seed(db, 2, None)

    detail = client.get("/api/v1/reports/2")
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == 2

    delete = client.delete("/api/v1/reports/2")
    assert delete.status_code == 200, delete.text
    with Session() as db:
        assert db.get(ReportInfo, 2) is None


def test_szbf_interpretation_and_indicators_hidden(env):
    client, Session = env
    with Session() as db:
        _seed(db, 3, "SZBF")

    interp = client.get("/api/v1/interpretations/3")
    indicators = client.get("/api/v1/interpretations/3/indicators")

    assert interp.status_code == 404
    assert indicators.status_code == 404


def test_normal_interpretation_and_indicators_still_work(env):
    client, Session = env
    with Session() as db:
        _seed(db, 4, None)

    interp = client.get("/api/v1/interpretations/4")
    assert interp.status_code == 200, interp.text
    assert interp.json()["report_id"] == 4

    indicators = client.get("/api/v1/interpretations/4/indicators")
    assert indicators.status_code == 200, indicators.text
    assert len(indicators.json()) == 1
