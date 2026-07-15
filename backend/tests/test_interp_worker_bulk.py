"""Task 10: interpretation worker per-queue consume + bulk 时段 + retry 队列接入测试."""
import pytest
from unittest.mock import patch, MagicMock

from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
# 触发所有表注册到 Base.metadata
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.interpretation.models import ReportInterpretation  # noqa: F401
from app.core.rabbitmq import _NackOnce
from app.core.retry import backoff_for_retry


def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


def _get_db_gen(session):
    yield session


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_int(
        ReportTask.__table__.c.id,
        ReportInfo.__table__.c.id,
        ReportIndicator.__table__.c.id,
        ReportInterpretation.__table__.c.id,
        ReportInterpretation.__table__.c.report_id,
    )
    Base.metadata.create_all(engine)
    _restore(saved)
    Session = sessionmaker(bind=engine)
    s = Session()

    Mq = MagicMock()
    getdb_p = patch("app.modules.interpretation.worker.get_hospital_db",
                   lambda hid: _get_db_gen(s))
    win_p = patch("app.modules.interpretation.worker.is_bulk_window_now",
                  return_value=True)
    agent_p = patch("app.modules.interpretation.worker.run_interpretation_agent")
    cmp_p = patch("app.modules.user_profile.service.try_generate_comparison_summary")
    batch_p = patch("app.modules.interpretation.worker.BatchService")
    mq_p = patch("app.modules.interpretation.worker.rabbitmq", Mq)

    agent_mock = agent_p.start()
    cmp_mock = cmp_p.start()
    batch_mock = batch_p.start()
    getdb_p.start(); win_p.start(); mq_p.start()
    try:
        yield s, Mq, agent_mock, cmp_mock, batch_mock
    finally:
        getdb_p.stop(); win_p.stop(); agent_p.stop(); cmp_p.stop()
        batch_p.stop(); mq_p.stop()
        s.close()


def _make_report(db, hospital_id="H001"):
    r = ReportInfo(user_id=1)
    db.add(r); db.commit(); db.refresh(r)
    return r


def _make_batch_file(db, batch_id="b1", file_id="f1", hospital_id="H001"):
    db.add(BatchImport(id=batch_id, hospital_id=hospital_id, user_id="1",
                      filename="x.zip", archive_path="/x"))
    db.add(BatchImportFile(id=file_id, batch_id=batch_id, file_path="/x/a.pdf",
                           file_size=10, crc32="abc12345"))
    db.commit()


# ---------------------------------------------------------------------------
# 1. bulk 非窗口 → _NackOnce(requeue=True)
# ---------------------------------------------------------------------------
def test_bulk_non_window_nack(env):
    s, Mq, agent_mock, cmp_mock, batch_mock = env
    r = _make_report(s)
    with patch("app.modules.interpretation.worker.is_bulk_window_now",
               return_value=False):
        from app.modules.interpretation.worker import handle_interpretation_task
        msg = {"_routing_key": "interpretation.bulk",
               "payload": {"report_id": r.id, "hospital_id": "H001"}}
        with pytest.raises(_NackOnce) as ei:
            handle_interpretation_task(msg)
        assert ei.value.requeue is True
    agent_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 2. normal 不受时段限制(非窗口仍处理)
# ---------------------------------------------------------------------------
def test_normal_not_time_bound(env):
    s, Mq, agent_mock, cmp_mock, batch_mock = env
    r = _make_report(s)
    agent_mock.return_value = {}
    with patch("app.modules.interpretation.worker.is_bulk_window_now",
               return_value=False):
        from app.modules.interpretation.worker import handle_interpretation_task
        msg = {"_routing_key": "interpretation.normal",
               "payload": {"report_id": r.id, "hospital_id": "H001"}}
        handle_interpretation_task(msg)
    agent_mock.assert_called_once()


# ---------------------------------------------------------------------------
# 3. retry_count<3 → publish_retry(routing_key=原, expiration=backoff(0))
# ---------------------------------------------------------------------------
def test_retry_count_1_publish_retry(env):
    s, Mq, agent_mock, cmp_mock, batch_mock = env
    r = _make_report(s)
    _make_batch_file(s)

    def _fail(hospital_id, db, report_id):
        interp = ReportInterpretation(report_id=report_id, status="pending",
                                      retry_count=1)
        db.add(interp); db.commit()
        raise RuntimeError("boom")

    agent_mock.side_effect = _fail

    from app.modules.interpretation.worker import handle_interpretation_task
    msg = {"_routing_key": "interpretation.normal",
           "payload": {"report_id": r.id, "hospital_id": "H001",
                       "batch_id": "b1", "file_id": "f1"}}
    handle_interpretation_task(msg)  # 不抛(走 retry,然后 return)

    Mq.publish_retry.assert_called_once()
    args, kwargs = Mq.publish_retry.call_args
    assert args[0] == "interpretation.normal"
    assert kwargs["expiration_ms"] == backoff_for_retry(0)
    assert kwargs.get("batch_id") == "b1"
    batch_mock.increment_progress.assert_not_called()  # 未 failed


# ---------------------------------------------------------------------------
# 4. retry_count>=3 → 抛异常 + increment failed
# ---------------------------------------------------------------------------
def test_retry_count_3_raises_and_failed(env):
    s, Mq, agent_mock, cmp_mock, batch_mock = env
    r = _make_report(s)
    _make_batch_file(s)

    def _fail(hospital_id, db, report_id):
        interp = ReportInterpretation(report_id=report_id, status="failed",
                                      retry_count=3)
        db.add(interp); db.commit()
        raise RuntimeError("boom")

    agent_mock.side_effect = _fail

    from app.modules.interpretation.worker import handle_interpretation_task
    msg = {"_routing_key": "interpretation.normal",
           "payload": {"report_id": r.id, "hospital_id": "H001",
                       "batch_id": "b1", "file_id": "f1"}}
    with pytest.raises(RuntimeError):
        handle_interpretation_task(msg)

    Mq.publish_retry.assert_not_called()
    batch_mock.increment_progress.assert_called_once_with(
        s, "b1", "f1", "failed")


# ---------------------------------------------------------------------------
# 5. 成功 + batch_id/file_id → increment interp_ok
# ---------------------------------------------------------------------------
def test_success_increments_interp_ok(env):
    s, Mq, agent_mock, cmp_mock, batch_mock = env
    r = _make_report(s)
    _make_batch_file(s)
    agent_mock.return_value = {}

    from app.modules.interpretation.worker import handle_interpretation_task
    msg = {"_routing_key": "interpretation.normal",
           "payload": {"report_id": r.id, "hospital_id": "H001",
                       "batch_id": "b1", "file_id": "f1"}}
    handle_interpretation_task(msg)

    batch_mock.increment_progress.assert_called_once_with(
        s, "b1", "f1", "interp_ok")
    Mq.publish_retry.assert_not_called()


# ---------------------------------------------------------------------------
# 6. comparison summary 失败不影响 interp 成功 + 进度计数
# ---------------------------------------------------------------------------
def test_comparison_summary_failure_doesnt_break(env):
    s, Mq, agent_mock, cmp_mock, batch_mock = env
    r = _make_report(s)
    _make_batch_file(s)
    agent_mock.return_value = {}
    cmp_mock.side_effect = RuntimeError("cmp boom")

    from app.modules.interpretation.worker import handle_interpretation_task
    msg = {"_routing_key": "interpretation.normal",
           "payload": {"report_id": r.id, "hospital_id": "H001",
                       "batch_id": "b1", "file_id": "f1"}}
    handle_interpretation_task(msg)

    batch_mock.increment_progress.assert_called_once_with(
        s, "b1", "f1", "interp_ok")
    Mq.publish_retry.assert_not_called()