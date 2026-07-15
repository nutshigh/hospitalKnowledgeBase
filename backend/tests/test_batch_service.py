import pytest
import uuid
import os
import tempfile
from unittest.mock import patch, MagicMock

from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.base import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _mock_publish():
    """Patch batch_service.rabbitmq with a MagicMock collecting TaskMessage instances.

    Uses start() (not `with`) so the patch persists across the caller's
    subsequent BatchService calls; stopped via the returned finalizer.
    """
    patcher = patch("app.modules.report.batch_service.rabbitmq")
    M = patcher.start()
    msgs = []
    M.publish.side_effect = lambda m: msgs.append(m)
    return patcher, msgs


def test_create_append_finalize_state_machine(db):
    patcher, msgs = _mock_publish()
    try:
        tmp = tempfile.mkdtemp()
        with patch("app.modules.report.batch_service.settings.FILE_STORAGE_ROOT", tmp):
            b = BatchService.create_batch(db, "H001", "admin", "import.zip")
            assert b.status == "uploading"
            for i in range(3):
                BatchService.append_chunk(db, b.id, i, 3, b"hello")
            BatchService.finalize_batch(db, b.id, None, 3, 15)
            db.refresh(b)
            assert b.status == "extracting"
        assert any(getattr(m, "task_type", "").endswith("extract") or m.task_type == "extract"
                   for m in msgs) or len(msgs) == 1
    finally:
        patcher.stop()


def test_handle_extracted_file_idempotent(db):
    db.add(BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x"))
    db.commit()
    fid1 = BatchService.handle_extracted_file(db, "b1", "a.pdf", "abc12345", 10)
    fid2 = BatchService.handle_extracted_file(db, "b1", "a.pdf", "abc12345", 10)
    assert fid1 == fid2  # 同 (batch,crc32) 返回同 id;total 不增加
    assert db.query(BatchImportFile).count() == 1


def test_increment_progress_idempotent(db):
    db.add(BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x"))
    db.add(BatchImportFile(id="f1", batch_id="b1", file_path="a", file_size=1, crc32="abc12345"))
    db.commit()
    BatchService.increment_progress(db, "b1", "f1", "parsed_ok")
    BatchService.increment_progress(db, "b1", "f1", "parsed_ok")  # 重复应不增加
    b = db.query(BatchImport).get("b1")
    assert b.parsed_ok == 1


def test_retry_failed_requeues(db):
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x", status="partial_failed", failed=1)
    f = BatchImportFile(id="f1", batch_id="b1", file_path="/x/a.pdf", file_size=1, crc32="abc12345", status="failed", error_message="x")
    db.add_all([b, f]); db.commit()
    patcher, msgs = _mock_publish()
    try:
        r = BatchService.retry_failed(db, "b1")
        assert r["requeued"] == 1
        db.refresh(f)
        assert f.status == "queued"
    finally:
        patcher.stop()


def test_status_complete_or_partial(db):
    # T1.6: all succeeded → completed
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x", total=2, parsed_ok=1, interp_ok=1)
    db.add(b); db.commit()
    BatchService._maybe_advance_status(db, b)
    db.refresh(b)
    assert b.status == "completed"
    # terminal state cannot transition (binding #6)
    b.failed = 1; b.interp_ok = 0; b.parsed_ok = 1
    BatchService._maybe_advance_status(db, b)
    db.refresh(b)
    assert b.status == "completed"

    # T1.7: some failed → partial_failed (fresh non-terminal batch)
    b2 = BatchImport(id="b2", hospital_id="H", user_id="u", filename="x", archive_path="/x",
                    total=2, parsed_ok=1, interp_ok=0, failed=1, status="parsing")
    db.add(b2); db.commit()
    BatchService._maybe_advance_status(db, b2)
    db.refresh(b2)
    assert b2.status == "partial_failed"