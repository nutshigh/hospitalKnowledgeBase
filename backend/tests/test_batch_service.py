import pytest
import uuid
import os
import tempfile
from unittest.mock import patch, MagicMock

from app.models.base import Base
# 触发 report/interpretation 表注册到 Base.metadata(retry_failed 懒导入这些模型)
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation  # noqa: F401
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
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


def test_retry_failed_interp_stage_routes_to_interp_bulk(db):
    """I3: interp 阶段失败的 file 重投 interpretation.bulk,只重置 ReportInterpretation,
    不清零 ReportTask.retry_count(parse 仍 OK)。"""
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x",
                    archive_path="/x", status="partial_failed", failed=1)
    # parse 已完成(ReportTask.status=completed),但 interp 失败
    task = ReportTask(id=100, user_id=1, original_file_path="/x/a.pdf",
                     original_filename="a.pdf", file_type="pdf", file_size=1,
                     status="completed", priority=0, retry_count=0)
    db.add(b); db.add(task); db.commit()
    report = ReportInfo(id=200, task_id=task.id, user_id=1)
    db.add(report); db.commit()
    interp = ReportInterpretation(id=300, report_id=report.id, status="failed", retry_count=3)
    db.add(interp); db.commit()
    f = BatchImportFile(id="f1", batch_id="b1", file_path="/x/a.pdf", file_size=1,
                       crc32="abc12345", status="failed", failed_stage="interpretation",
                       report_task_id=task.id, error_message="interp boom")
    db.add(f); db.commit()

    patcher, msgs = _mock_publish()
    try:
        r = BatchService.retry_failed(db, "b1")
        assert r["requeued"] == 1
        db.refresh(f)
        assert f.status == "queued"
        assert f.failed_stage is None
        # 重投 interpretation.bulk,带正确 report_id
        interp_msgs = [m for m in msgs if m.task_type == "interpretation"]
        assert len(interp_msgs) == 1
        assert interp_msgs[0].priority == "bulk"
        assert interp_msgs[0].payload["report_id"] == report.id
        assert interp_msgs[0].payload["file_id"] == "f1"
        # 不应重投 parsing(parse 已 OK)
        assert not any(m.task_type == "parsing" for m in msgs)
        # ReportTask.retry_count 未被清零
        db.refresh(task)
        assert task.retry_count == 0
        # ReportInterpretation 已重置为 pending
        db.refresh(interp)
        assert interp.status == "pending"
        assert interp.retry_count == 0
        # batch 回到 interpreting 态
        db.refresh(b)
        assert b.status == "interpreting"
        assert b.failed == 0
    finally:
        patcher.stop()


def test_retry_failed_parsing_stage_routes_to_parsing_bulk(db):
    """I3: parsing 阶段失败的 file 重投 parsing.bulk,重置 ReportTask.retry_count。"""
    from app.core.rabbitmq import TaskMessage
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x",
                    archive_path="/x", status="partial_failed", failed=1)
    task = ReportTask(id=100, user_id=1, original_file_path="/x/a.pdf",
                     original_filename="a.pdf", file_type="pdf", file_size=1,
                     status="failed", priority=0, retry_count=3,
                     error_message="parse boom")
    db.add(b); db.add(task); db.commit()
    f = BatchImportFile(id="f1", batch_id="b1", file_path="/x/a.pdf", file_size=1,
                       crc32="abc12345", status="failed", failed_stage="parsing",
                       report_task_id=task.id, error_message="parse boom")
    db.add(f); db.commit()

    patcher, msgs = _mock_publish()
    try:
        r = BatchService.retry_failed(db, "b1")
        assert r["requeued"] == 1
        db.refresh(f)
        assert f.status == "queued" and f.failed_stage is None
        parse_msgs = [m for m in msgs if m.task_type == "parsing"]
        assert len(parse_msgs) == 1
        assert parse_msgs[0].priority == "bulk"
        assert parse_msgs[0].payload["task_id"] == task.id
        db.refresh(task)
        assert task.status == "queued" and task.retry_count == 0
        db.refresh(b)
        assert b.status == "parsing" and b.failed == 0
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


def test_retry_failed_skips_dispatch_unmatched_and_oversize(db):
    """retry_failed 对 oversize / dispatch_unmatched 两类 unretryable
    短路跳过,不计入 requeued,只在 skipped_unretryable 计数。
    无 report_task_id 的两类不会 publish。"""
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x",
                    archive_path="/x", status="partial_failed", failed=2)
    f_unm = BatchImportFile(id="funm", batch_id="b1", file_path="/x/report.pdf",
                            file_size=1, crc32="aaaa1111",
                            status="failed", failed_stage="dispatch_unmatched",
                            error_message="dispatch_unmatched")
    f_oversize = BatchImportFile(id="fovz", batch_id="b1", file_path="/x/big.pdf",
                                 file_size=99, crc32="bbbb2222",
                                 status="failed", failed_stage="oversize",
                                 error_message="oversize")
    db.add_all([b, f_unm, f_oversize]); db.commit()

    patcher, msgs = _mock_publish()
    try:
        r = BatchService.retry_failed(db, "b1")
        assert r["requeued"] == 0
        assert r["skipped_unretryable"] == 2
        # 两个 file 行的 status 仍是 failed(未被重置为 queued)
        db.refresh(f_unm); db.refresh(f_oversize)
        assert f_unm.status == "failed"
        assert f_oversize.status == "failed"
        # 没有 publish
        assert msgs == []
        # batch 状态未变 partial_failed,failed 未扣减
        db.refresh(b)
        assert b.status == "partial_failed"
        assert b.failed == 2
    finally:
        patcher.stop()


def test_retry_failed_mixed_retryable_and_unretryable(db):
    """混合场景:1 个 dispatch_unmatched + 1 个 parsing 失败 → 重投 1,
    skipped_unretryable=1, batch failed 扣减 1。"""
    from app.modules.report.models import ReportTask
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x",
                    archive_path="/x", status="partial_failed", failed=2)
    f_unm = BatchImportFile(id="funm", batch_id="b1", file_path="/x/report.pdf",
                            file_size=1, crc32="aaaa1111", status="failed",
                            failed_stage="dispatch_unmatched",
                            error_message="dispatch_unmatched")
    task = ReportTask(id=100, user_id=1, original_file_path="/x/a.pdf",
                      original_filename="a.pdf", file_type="pdf", file_size=1,
                      status="failed", priority=0, retry_count=3,
                      error_message="parse boom")
    db.add(b); db.add(task); db.commit()
    f_parse = BatchImportFile(id="fparse", batch_id="b1",
                              file_path="/x/a.pdf", file_size=1,
                              crc32="bbbb2222", status="failed",
                              failed_stage="parsing",
                              report_task_id=task.id,
                              error_message="parse boom")
    db.add_all([f_unm, f_parse]); db.commit()

    patcher, msgs = _mock_publish()
    try:
        r = BatchService.retry_failed(db, "b1")
        assert r["requeued"] == 1
        assert r["skipped_unretryable"] == 1
        # parsing 失败被重投;report_task 重置
        parse_msgs = [m for m in msgs if m.task_type == "parsing"]
        assert len(parse_msgs) == 1
        db.refresh(task)
        assert task.status == "queued" and task.retry_count == 0
        # dispatch_unmatched 文件保持 failed
        db.refresh(f_unm)
        assert f_unm.status == "failed"
        # batch status 从 partial_failed → parsing(requeued>0 触发)
        db.refresh(b)
        assert b.status == "parsing"
        assert b.failed == 1  # 扣减了 1(requeued=1)
    finally:
        patcher.stop()