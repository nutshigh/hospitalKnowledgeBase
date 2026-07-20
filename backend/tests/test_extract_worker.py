"""T2.1–T2.7 extract_worker 用例(Spec §7.2)."""
import io
import os
import tarfile
import tempfile
import zipfile
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import Integer

from app.models.base import Base
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService
# 触发 ReportTask/ReportInfo/ReportIndicator 注册到 Base.metadata(create_task 要写)
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401


# sqlite 上 BigInteger PRIMARY KEY 不会自动取 rowid(只 INTEGER PRIMARY KEY 才行)。
# 这里在创建表前把需要自增的主键改成 Integer(测试期临时变异,不污染生产 DDL)。
def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


def _make_zip(path, files):
    """files: list[(name, content_bytes)]"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)


def _make_tar(path, files):
    with tarfile.open(path, "w") as tf:
        for name, data in files:
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            import io as _io
            tf.addfile(ti, _io.BytesIO(data))


def _get_db_gen(session):
    yield session


@pytest.fixture
def env():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

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

    tmp = tempfile.mkdtemp()
    Mq = MagicMock()
    msgs = []
    Mq.publish.side_effect = lambda m: msgs.append(m)

    getdb_p = patch("app.modules.report.extract_worker.get_hospital_db",
                    lambda hid: _get_db_gen(s))
    bs_settings = patch("app.modules.report.batch_service.settings.FILE_STORAGE_ROOT", tmp)
    ew_settings = patch("app.modules.report.extract_worker.settings.FILE_STORAGE_ROOT", tmp)
    bs_mq = patch("app.modules.report.batch_service.rabbitmq", Mq)
    ew_mq = patch("app.modules.report.extract_worker.rabbitmq", Mq)
    svc_mq = patch("app.modules.report.service.rabbitmq", Mq)

    getdb_p.start(); bs_settings.start(); ew_settings.start()
    bs_mq.start(); ew_mq.start(); svc_mq.start()
    try:
        yield s, tmp, Mq, msgs
    finally:
        getdb_p.stop(); bs_settings.stop(); ew_settings.stop()
        bs_mq.stop(); ew_mq.stop(); svc_mq.stop()
        s.close()


def _make_batch(env, archive_path, status="extracting", hospital_id="H001", user_id="123"):
    db = env[0]
    b = BatchImport(id="b1", hospital_id=hospital_id, user_id=user_id,
                    filename="x.zip", archive_path=archive_path, status=status)
    db.add(b); db.commit()
    return b


def _msg(batch_id="b1", archive_path=None):
    return {"payload": {"batch_id": batch_id, "hospital_id": "H001",
                       "archive_path": archive_path}}


# ---------------------------------------------------------------------------
# T2.1 zip(3 pdf) → 3 file 行 + 3 publish
# ---------------------------------------------------------------------------
def test_T2_1_three_pdfs(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("u1_H001_1.pdf", b"pdf1"), ("u2_H001_2.pdf", b"pdf2"), ("u3_H001_3.pdf", b"pdf3")])
    _make_batch(env, ap)

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    assert db.query(BatchImportFile).count() == 3
    assert len(msgs) == 3
    for m in msgs:
        assert m.priority == "bulk"
        assert m.routing_key() == "parsing.bulk"
    db.refresh(db.query(BatchImport).get("b1"))
    assert db.query(BatchImport).get("b1").status == "parsing"


# ---------------------------------------------------------------------------
# T2.2 zip 炸弹(单 > 50MB) → skip + failed='oversize'
# ---------------------------------------------------------------------------
def test_T2_2_oversize(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    # patch a tiny limit
    from app.config import settings
    with patch.object(settings, "BATCH_FILE_MAX_SIZE", 10):
        _make_zip(ap, [("big_H001_1.pdf", b"x" * 200)])
        _make_batch(env, ap)
        from app.modules.report.extract_worker import handle_extract_task
        handle_extract_task(_msg(archive_path=ap))

    b1 = db.query(BatchImport).get("b1")
    assert b1.failed == 1
    f = db.query(BatchImportFile).first()
    assert f.status == "failed"
    assert f.error_message == "oversize"
    assert len(msgs) == 0  # 不投 parsing
    assert db.query(BatchImport).get("b1").status == "partial_failed"  # 0 valid


# ---------------------------------------------------------------------------
# T2.3 混合扩展名:pdf/jpg/png 通过,docx/.txt 跳过 (doc 不在此例,白名单含 doc)
# ---------------------------------------------------------------------------
def test_T2_3_mixed_exts(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [
        ("ok1_H001_1.pdf", b"pdf"),
        ("ok2_H001_2.jpg", b"jpg"),
        ("ok3_H001_3.png", b"png"),
        ("skip1.docx", b"docx"),
        ("skip2.txt", b"txt"),
    ])
    _make_batch(env, ap)

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    q = db.query(BatchImportFile)
    assert q.count() == 3
    for f in q.all():
        assert os.path.splitext(f.file_path)[1].lower() in (".pdf", ".jpg", ".png")
    assert len(msgs) == 3


# ---------------------------------------------------------------------------
# T2.4 同 crc32 两文件 → 1 file,不重复 publish
# ---------------------------------------------------------------------------
def test_T2_4_dup_crc32(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("u1_H001_1.pdf", b"same"), ("u2_H001_2.pdf", b"same")])
    _make_batch(env, ap)

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    assert db.query(BatchImportFile).count() == 1
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# T2.5 batch='cancelled' → 不 publish 且 ack
# ---------------------------------------------------------------------------
def test_T2_5_cancelled_skipped(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("cn_H001_1.pdf", b"x")])
    _make_batch(env, ap, status="cancelled")

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    assert db.query(BatchImportFile).count() == 0
    assert len(msgs) == 0


# ---------------------------------------------------------------------------
# T2.6 重投 extract.task → 只补差(不重复 publish)
# ---------------------------------------------------------------------------
def test_T2_6_requeue_only_fills_gap(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("q1_H001_1.pdf", b"x"), ("q2_H001_2.pdf", b"y")])
    _make_batch(env, ap)

    from app.modules.report.extract_worker import handle_extract_task
    msg = _msg(archive_path=ap)
    handle_extract_task(msg)        # first run: 2 files, 2 publishes
    assert len(msgs) == 2
    n1 = db.query(BatchImportFile).count()
    assert n1 == 2

    handle_extract_task(msg)        # re-run: dedupe, no new publish
    assert db.query(BatchImportFile).count() == 2
    assert len(msgs) == 2            # unchanged


# ---------------------------------------------------------------------------
# T2.7 损坏 zip → partial_failed
# ---------------------------------------------------------------------------
def test_T2_7_corrupt_zip(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    with open(ap, "wb") as fh:
        fh.write(b"not a zip file")
    _make_batch(env, ap)

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    b1 = db.query(BatchImport).get("b1")
    assert b1.status == "partial_failed"
    assert "archive_corrupt" in (b1.error_message or "")
    assert len(msgs) == 0


# ---------------------------------------------------------------------------
# T2.8 瞬时异常(retry_count<3)→ publish_retry("extract.bulk"),batch 仍 extracting
# ---------------------------------------------------------------------------
def test_T2_8_transient_retry_publish_retry(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("t1_H001_1.pdf", b"x")])
    _make_batch(env, ap)

    from app.modules.report import extract_worker
    with patch.object(extract_worker, "_extract_and_enqueue",
                      side_effect=RuntimeError("transient blip")):
        handle_extract_task = extract_worker.handle_extract_task
        handle_extract_task(_msg(archive_path=ap))

    Mq.publish_retry.assert_called_once()
    args, kwargs = Mq.publish_retry.call_args
    assert args[0] == "extract.bulk"
    assert kwargs.get("batch_id") == "b1"
    # retry 队列存了带 retry_count 的副本
    import json as _json
    body = args[1]
    parsed = _json.loads(body)
    assert parsed["payload"]["retry_count"] == 1
    # batch 不进终态,仍 extracting(等延迟副本回流)
    db.refresh(db.query(BatchImport).get("b1"))
    assert db.query(BatchImport).get("b1").status == "extracting"
    assert len(msgs) == 0  # 未 publish 新 parsing task


# ---------------------------------------------------------------------------
# T2.9 瞬时异常 retry_count>=3 → partial_failed, 不再 publish_retry
# ---------------------------------------------------------------------------
def test_T2_9_transient_retry_exhausted_partial_failed(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("t1_H001_1.pdf", b"x")])
    _make_batch(env, ap)

    from app.modules.report import extract_worker
    with patch.object(extract_worker, "_extract_and_enqueue",
                      side_effect=RuntimeError("transient blip")):
        handle_extract_task = extract_worker.handle_extract_task
        # payload 已带 retry_count=2 → 本次 +1 = 3 → 终态 partial_failed
        msg = {"payload": {"batch_id": "b1", "hospital_id": "H001",
                           "archive_path": ap, "retry_count": 2}}
        handle_extract_task(msg)

    Mq.publish_retry.assert_not_called()
    b1 = db.query(BatchImport).get("b1")
    assert b1.status == "partial_failed"
    assert "extract_failed_after_retries" in (b1.error_message or "")


# ---------------------------------------------------------------------------
# T2.10 _resolve_user_id: 三段命名命中
# ---------------------------------------------------------------------------
def test_resolve_user_id_matches_three_segment():
    from app.modules.report.extract_worker import _resolve_user_id
    assert _resolve_user_id("张三_H001_1001.pdf") == 1001
    assert _resolve_user_id("LiSi_H002_2048.pdf") == 2048
    # 路径前缀 OK(basename 拆解)
    assert _resolve_user_id("sub/dir/王五_H003_7.pdf") == 7


# ---------------------------------------------------------------------------
# T2.11 _resolve_user_id: 反例不命中(返回 None)
# ---------------------------------------------------------------------------
def test_resolve_user_id_rejects_non_numeric_or_missing_segment():
    from app.modules.report.extract_worker import _resolve_user_id
    assert _resolve_user_id("1001.pdf") is None              # 只 1 段
    assert _resolve_user_id("张三_1001.pdf") is None         # 只 2 段
    assert _resolve_user_id("张三_H001_abc.pdf") is None     # 末段非数字
    assert _resolve_user_id("张三_H001_1001_extra.pdf") is None  # 4 段
    assert _resolve_user_id("张三H0011001.pdf") is None      # 无下划线
    assert _resolve_user_id(".pdf") is None                 # 空 basename


# ---------------------------------------------------------------------------
# T2.12 zip 内文件名不合规 → file.failed_stage='dispatch_unmatched',不投 parsing
# ---------------------------------------------------------------------------
def test_T2_12_dispatch_unmatched(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("report.pdf", b"x")])  # 无下划线
    _make_batch(env, ap)

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    f = db.query(BatchImportFile).one()
    assert f.status == "failed"
    assert f.failed_stage == "dispatch_unmatched"
    assert f.error_message == "dispatch_unmatched"
    assert f.report_task_id is None        # 不 create_task
    assert len(msgs) == 0                  # 不投 parsing
    b1 = db.query(BatchImport).get("b1")
    assert b1.failed == 1
    assert b1.status == "partial_failed"   # 全 unmatched → partial_failed


# ---------------------------------------------------------------------------
# T2.13 命中三段命名 → report_task.user_id == 文件名第 3 段(而非上传者 b.user_id)
# ---------------------------------------------------------------------------
def test_T2_13_dispatch_uses_filename_user_id(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("张三_H001_1001.pdf", b"x"), ("李四_H002_2048.pdf", b"y")])
    _make_batch(env, ap, user_id="999")  # 上传者 admin user_id=999

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    from app.modules.report.models import ReportTask
    tasks = db.query(ReportTask).order_by(ReportTask.id).all()
    assert len(tasks) == 2
    assert {t.user_id for t in tasks} == {1001, 2048}
    assert 999 not in {t.user_id for t in tasks}
    file_rows = db.query(BatchImportFile).order_by(BatchImportFile.id).all()
    for f in file_rows:
        assert f.report_task_id is not None
    assert len(msgs) == 2


def test_extract_worker_logger_namespaced_under_app_batch():
    """extract_worker.py 模块级 logger 应为 app.batch.extract。"""
    import app.modules.report.extract_worker as mod
    assert mod._log.name == "app.batch.extract", (
        f"expected app.batch.extract, got {mod._log.name}"
    )


def test_extract_worker_start_worker_calls_setup_logging(monkeypatch):
    """start_worker() 第一行应调用 setup_logging()。"""
    import pytest
    import app.modules.report.extract_worker as mod

    calls = {"n": 0}

    def _spy(default_level="INFO"):
        calls["n"] += 1

    # patch the binding in the worker module, not the source module,
    # because `from ... import setup_logging` makes a separate binding.
    monkeypatch.setattr(mod, "setup_logging", _spy)
    # consume 在 try 块里返回 None 即可;start_consuming 用 SystemExit
    # 跳出 start_worker 的 except Exception(不会被 RuntimeError 触发到)
    monkeypatch.setattr(mod.rabbitmq, "consume", lambda *a, **k: None)
    monkeypatch.setattr(
        mod.rabbitmq, "start_consuming",
        lambda *a, **k: (_ for _ in ()).throw(SystemExit("stop-loop")),
    )

    with pytest.raises(SystemExit):
        mod.start_worker()
    assert calls["n"] >= 1, "start_worker 必须先调 setup_logging()"