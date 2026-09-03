"""Task 9: report worker per-queue consume + bulk 时段 + retry 队列接入测试."""
import pytest
from unittest.mock import patch, MagicMock

from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
# 触发所有表注册到 Base.metadata
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401
from app.modules.report.batch_models import BatchImport, BatchImportFile
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
    )
    Base.metadata.create_all(engine)
    _restore(saved)
    Session = sessionmaker(bind=engine)
    s = Session()

    Mq = MagicMock()
    getdb_p = patch("app.modules.report.worker.get_hospital_db",
                   lambda hid: _get_db_gen(s))
    win_p = patch("app.modules.report.worker.is_bulk_window_now", return_value=True)
    svc_p = patch("app.modules.report.worker.process_task")
    mq_p = patch("app.modules.report.worker.rabbitmq", Mq)

    svc_mock = svc_p.start()
    getdb_p.start(); win_p.start(); mq_p.start()
    try:
        yield s, Mq, svc_mock
    finally:
        getdb_p.stop(); win_p.stop(); svc_p.stop(); mq_p.stop()
        s.close()


def _make_task(db, retry_count=0, status="queued", hospital_id="H001"):
    t = ReportTask(
        user_id="123456", original_file_path="/tmp/x.pdf", original_filename="x.pdf",
        file_type="pdf", file_size=10, status=status,
        priority=0, retry_count=retry_count,
    )
    db.add(t); db.commit(); db.refresh(t)
    return t


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
    s, Mq, svc_p = env
    t = _make_task(s)
    with patch("app.modules.report.worker.is_bulk_window_now", return_value=False):
        from app.modules.report.worker import handle_parsing_task
        msg = {"_routing_key": "parsing.bulk",
               "payload": {"task_id": t.id, "hospital_id": "H001"}}
        with pytest.raises(_NackOnce) as ei:
            handle_parsing_task(msg)
        assert ei.value.requeue is True
    svc_p.assert_not_called()


# ---------------------------------------------------------------------------
# 2. normal 不受时段限制(非窗口仍处理)
# ---------------------------------------------------------------------------
def test_normal_not_time_bound(env):
    s, Mq, svc_p = env
    t = _make_task(s)
    _make_batch_file(s)
    svc_p.return_value = None  # success
    with patch("app.modules.report.worker.is_bulk_window_now", return_value=False):
        from app.modules.report.worker import handle_parsing_task
        msg = {"_routing_key": "parsing.normal",
               "payload": {"task_id": t.id, "hospital_id": "H001",
                           "batch_id": "b1", "file_id": "f1"}}
        handle_parsing_task(msg)
    b = s.query(BatchImport).get("b1")
    assert b.parsed_ok == 1


# ---------------------------------------------------------------------------
# 3. retry_count=1 → publish_retry(routing_key=原, expiration=backoff(0))
# ---------------------------------------------------------------------------
def test_retry_count_1_publish_retry(env):
    s, Mq, svc_p = env
    t = _make_task(s, retry_count=0)
    _make_batch_file(s)

    def _fail(db, task_id, hospital_id, batch_id=None, file_id=None,
              batch_hospital_id=None):
        tt = s.query(ReportTask).get(task_id)
        tt.retry_count += 1
        tt.status = "queued"
        s.commit()
        raise RuntimeError("boom")

    svc_p.side_effect = _fail

    from app.modules.report.worker import handle_parsing_task
    msg = {"_routing_key": "parsing.normal",
           "payload": {"task_id": t.id, "hospital_id": "H001",
                       "batch_id": "b1", "file_id": "f1"}}
    handle_parsing_task(msg)  # 不抛(走 retry,然后 return)

    Mq.publish_retry.assert_called_once()
    args, kwargs = Mq.publish_retry.call_args
    assert args[0] == "parsing.normal"
    assert kwargs["expiration_ms"] == backoff_for_retry(0)
    assert kwargs.get("batch_id") == "b1"
    # file 未被计 failed
    b = s.query(BatchImport).get("b1")
    assert (b.failed or 0) == 0
    f = s.query(BatchImportFile).get("f1")
    assert f.status == "queued"


# ---------------------------------------------------------------------------
# 4. retry_count>=3 → 抛异常 + increment failed
# ---------------------------------------------------------------------------
def test_retry_count_3_raises_and_failed(env):
    s, Mq, svc_p = env
    t = _make_task(s, retry_count=2)  # 本次 process 会增到 3
    _make_batch_file(s)

    def _fail(db, task_id, hospital_id, batch_id=None, file_id=None,
              batch_hospital_id=None):
        tt = s.query(ReportTask).get(task_id)
        tt.retry_count += 1
        tt.status = "failed"
        s.commit()
        raise RuntimeError("boom")

    svc_p.side_effect = _fail

    from app.modules.report.worker import handle_parsing_task
    msg = {"_routing_key": "parsing.normal",
           "payload": {"task_id": t.id, "hospital_id": "H001",
                       "batch_id": "b1", "file_id": "f1"}}
    with pytest.raises(RuntimeError):
        handle_parsing_task(msg)

    Mq.publish_retry.assert_not_called()  # 不再走 retry
    b = s.query(BatchImport).get("b1")
    assert b.failed == 1
    f = s.query(BatchImportFile).get("f1")
    assert f.status == "failed"


# ---------------------------------------------------------------------------
# 5. 成功 + batch_id/file_id → increment parsed_ok
# ---------------------------------------------------------------------------
def test_success_increments_parsed_ok(env):
    s, Mq, svc_p = env
    t = _make_task(s)
    _make_batch_file(s)
    svc_p.return_value = None

    from app.modules.report.worker import handle_parsing_task
    msg = {"_routing_key": "parsing.normal",
           "payload": {"task_id": t.id, "hospital_id": "H001",
                       "batch_id": "b1", "file_id": "f1"}}
    handle_parsing_task(msg)

    b = s.query(BatchImport).get("b1")
    assert b.parsed_ok == 1
    f = s.query(BatchImportFile).get("f1")
    assert f.status == "parsed"
    Mq.publish_retry.assert_not_called()


def test_report_worker_has_app_parse_logger():
    """report/worker.py 应预留 app.parse logger。"""
    import app.modules.report.worker as mod
    assert mod._log.name == "app.parse"


def test_report_worker_start_worker_calls_setup_logging(monkeypatch):
    """start_worker() 第一行应调用 setup_logging()。"""
    import pytest
    import app.modules.report.worker as mod

    calls = {"n": 0}

    def _spy(default_level="INFO"):
        calls["n"] += 1

    monkeypatch.setattr(mod, "setup_logging", _spy)
    monkeypatch.setattr(mod.rabbitmq, "consume", lambda *a, **k: None)
    monkeypatch.setattr(
        mod.rabbitmq, "start_consuming",
        lambda *a, **k: (_ for _ in ()).throw(SystemExit("stop-loop")),
    )

    with pytest.raises(SystemExit):
        mod.start_worker()
    assert calls["n"] >= 1