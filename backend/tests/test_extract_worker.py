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
    _make_zip(ap, [("a.pdf", b"pdf1"), ("b.pdf", b"pdf2"), ("c.pdf", b"pdf3")])
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
        _make_zip(ap, [("big.pdf", b"x" * 200)])
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
        ("ok1.pdf", b"pdf"),
        ("ok2.jpg", b"jpg"),
        ("ok3.png", b"png"),
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
    _make_zip(ap, [("a.pdf", b"same"), ("b.pdf", b"same")])
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
    _make_zip(ap, [("a.pdf", b"x")])
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
    _make_zip(ap, [("a.pdf", b"x"), ("b.pdf", b"y")])
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