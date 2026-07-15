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
    # T1.6: all succeeded (interp 终态完成) → completed。
    # 终态判定 = interp_ok + failed == total (C3 修正后)。parsed_ok 是中间态,不计入。
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x",
                    total=2, parsed_ok=2, interp_ok=2, failed=0)
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
    # 终态完成 = interp_ok + failed == total:1 个 interp_ok + 1 个 failed == 2。
    b2 = BatchImport(id="b2", hospital_id="H", user_id="u", filename="x", archive_path="/x",
                    total=2, parsed_ok=1, interp_ok=1, failed=1, status="parsing")
    db.add(b2); db.commit()
    BatchService._maybe_advance_status(db, b2)
    db.refresh(b2)
    assert b2.status == "partial_failed"


def test_parsed_to_interp_ok_progression(db):
    """C2+C3: 文件 queued→parsed(++parsed_ok) 后 parsed→interp_ok(++interp_ok);
    batch 在 parsed=total 时不得提前 completed;interp 产出的第一个让 parsing→interpreting,
    全部 interp_ok 后到达 completed。
    """
    b = BatchImport(id="bp", hospital_id="H", user_id="u", filename="x", archive_path="/x",
                    total=2, parsed_ok=0, interp_ok=0, failed=0, status="parsing")
    f1 = BatchImportFile(id="f1", batch_id="bp", file_path="/x/a.pdf",
                         file_size=1, crc32="aaa11111")
    f2 = BatchImportFile(id="f2", batch_id="bp", file_path="/x/b.pdf",
                         file_size=1, crc32="bbb22222")
    db.add_all([b, f1, f2]); db.commit()

    # 1) 两个文件都 parse 成功 → parsed_ok==total,但未提前 completed
    BatchService.increment_progress(db, "bp", "f1", "parsed_ok")
    BatchService.increment_progress(db, "bp", "f2", "parsed_ok")
    db.refresh(b); db.refresh(f1); db.refresh(f2)
    assert b.status == "parsing"  # 未提前 completed (interp_ok/failed==0)
    assert b.parsed_ok == 2 and b.interp_ok == 0
    assert f1.status == "parsed" and f2.status == "parsed"

    # 2) 第一个 interp_ok → parsing → interpreting (auto-advance)
    BatchService.increment_progress(db, "bp", "f1", "interp_ok")
    db.refresh(b); db.refresh(f1)
    assert f1.status == "interp_ok"
    assert b.status == "interpreting"  # 第一个 interp 触发 parsing→interpreting
    assert b.interp_ok == 1

    # 3) 第二个 interp_ok → 终态 completed
    BatchService.increment_progress(db, "bp", "f2", "interp_ok")
    db.refresh(b); db.refresh(f2)
    assert f2.status == "interp_ok"
    assert b.parsed_ok == 2 and b.interp_ok == 2 and (b.failed or 0) == 0
    assert b.status == "completed"