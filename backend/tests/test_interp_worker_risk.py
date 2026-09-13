"""解读成功路径会 publish risk.hit 消息(含 report_id / interpretation_id)。"""
from unittest.mock import patch

from app.modules.report.models import ReportInfo
from app.modules.interpretation.models import ReportInterpretation
from app.modules.report.batch_models import BatchImport, BatchImportFile

from tests.test_interp_worker_bulk import _swap_int, _restore
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker
from app.models.base import Base


def _get_db_gen(session):
    yield session


def _make_report(db):
    r = ReportInfo(user_id=1)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _make_completed_interp(db, report_id):
    i = ReportInterpretation(report_id=report_id, status="completed", retry_count=0)
    db.add(i)
    db.commit()
    db.refresh(i)
    return i


def test_interp_ok_publishes_risk():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_int(
        ReportInfo.__table__.c.id,
        ReportInterpretation.__table__.c.id,
        ReportInterpretation.__table__.c.report_id,
    )
    Base.metadata.create_all(engine)
    _restore(saved)
    s = sessionmaker(bind=engine)()
    r = _make_report(s)

    def _agent(hospital_id, db, report_id):
        return _make_completed_interp(db, report_id)

    mq = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    patches = [
        patch("app.modules.interpretation.worker.get_hospital_db",
              lambda hid: _get_db_gen(s)),
        patch("app.modules.interpretation.worker.run_interpretation_agent",
              side_effect=_agent),
        patch("app.modules.user_profile.service.try_generate_comparison_summary"),
        patch("app.modules.interpretation.worker.BatchService"),
        patch("app.modules.interpretation.worker.rabbitmq", mq),
    ]
    for p in patches:
        p.start()
    try:
        from app.modules.interpretation.worker import handle_interpretation_task
        msg = {"_routing_key": "interpretation.normal",
               "hospital_id": "H001",
               "payload": {"report_id": r.id}}
        handle_interpretation_task(msg)
    finally:
        for p in patches:
            p.stop()
        s.close()

    published = [c for c in mq.method_calls if c[0] == "publish"]
    assert len(published) == 1
    task = published[0].args[0]
    assert task.task_type == "risk"
    assert task.payload["report_id"] == r.id
    assert task.payload["interpretation_id"] is not None
