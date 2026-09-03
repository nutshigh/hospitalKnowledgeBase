"""跨院分发(上传方医院 ≠ 文件目标医院)的批次进度与重试路由测试。

背景(2026-09-03 修复):批量上传按文件名解析出目标医院 orgId,报告 task/report 落在
目标医院库;而 BatchImport/BatchImportFile/进度计数器写在上传方(批次)库。若上传方
与目标医院不同(跨院分发),parsing/解读 worker 原来用目标库记批次进度 → file_not_found,
批次永远卡 parsing。修复:消息携带 batch_hospital_id,worker 到批次库记进度;file 行记
dispatch_hospital,重试时到目标库定位 report_task/report。
"""
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Integer, String, create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401


def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _swap_str(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = String(64)
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


def _get_db_gen(session):
    yield session


# ---------------------------------------------------------------------------
# BatchService.update_batch_progress:同库用 fallback,跨库另开批次库会话
# ---------------------------------------------------------------------------
def test_update_batch_progress_same_hospital_uses_fallback_db():
    from app.modules.report.batch_service import BatchService
    fallback = MagicMock()
    with patch.object(BatchService, "increment_progress") as inc:
        BatchService.update_batch_progress("H001", "H001", fallback, "b1", "f1", "parsed_ok")
    inc.assert_called_once_with(fallback, "b1", "f1", "parsed_ok")


def test_update_batch_progress_cross_hospital_opens_batch_db():
    from app.modules.report.batch_service import BatchService
    fallback = MagicMock()
    batch_db = MagicMock()
    with patch("app.modules.report.batch_service.get_hospital_db",
               lambda hid: _get_db_gen(batch_db)), \
         patch.object(BatchService, "increment_progress") as inc:
        BatchService.update_batch_progress("H001", "1", fallback, "b1", "f1",
                                           "interp_ok")
    inc.assert_called_once_with(batch_db, "b1", "f1", "interp_ok")
    batch_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# retry_failed 跨院:file 在批次库 A(H001),任务在目标库 B(org "1")
# ---------------------------------------------------------------------------
@pytest.fixture
def two_dbs():
    saved_int = _swap_int(
        ReportTask.__table__.c.id,
        ReportInfo.__table__.c.id,
        ReportIndicator.__table__.c.id,
    )
    saved_str = _swap_str(
        ReportTask.__table__.c.user_id,
        ReportInfo.__table__.c.user_id,
    )
    e1 = create_engine("sqlite:///:memory:")
    e2 = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e1)
    Base.metadata.create_all(e2)
    _restore(saved_int)
    _restore(saved_str)
    S = sessionmaker()
    A = S(bind=e1)   # 上传方/批次库 H001
    B = S(bind=e2)   # 目标库 "1"
    try:
        yield A, B, e1, e2, S
    finally:
        A.close()
        B.close()


def test_retry_failed_cross_hospital_routes_to_target_db(two_dbs):
    A, B, e1, e2, S = two_dbs
    from app.modules.report.batch_service import BatchService

    # 目标库 B(org "1")里有一个失败的解析任务
    task = ReportTask(user_id="011234", original_file_path="/tmp/x.pdf",
                      original_filename="张三_011234.pdf", file_type="pdf",
                      file_size=10, status="failed", retry_count=3)
    B.add(task)
    B.commit()

    # 批次库 A(H001):batch + 失败 file,指向 B 里的 task id
    A.add(BatchImport(id="b1", hospital_id="H001", user_id="3",
                      filename="x.zip", archive_path="/x", status="partial_failed",
                      total=1, parsed_ok=0, interp_ok=0, failed=1))
    A.add(BatchImportFile(id="f1", batch_id="b1", file_path="张三_011234.pdf",
                          file_size=10, crc32="abc12345", status="failed",
                          failed_stage="parsing", report_task_id=task.id,
                          dispatch_hospital="1"))
    A.commit()

    Mq = MagicMock()

    def _fresh_db(hid):
        return _get_db_gen(S(bind=e2 if hid == "1" else e1))

    with patch("app.modules.report.batch_service.get_hospital_db", _fresh_db), \
         patch("app.modules.report.batch_service.rabbitmq", Mq):
        BatchService.retry_failed(A, "b1")

    # 1) 重新投递到目标医院 "1" 的 parsing 队列
    assert Mq.publish.call_count == 1
    msg = Mq.publish.call_args[0][0]
    assert msg.hospital_id == "1"
    assert msg.task_type == "parsing"
    assert msg.payload["task_id"] == task.id
    assert msg.payload["batch_hospital_id"] == "H001"

    # 2) 目标库的任务被重置为 queued
    B.expire_all()
    assert B.query(ReportTask).get(task.id).status == "queued"
    assert B.query(ReportTask).get(task.id).retry_count == 0

    # 3) 批次库的 file 回到 queued、失败计数清掉
    A.expire_all()
    f = A.query(BatchImportFile).get("f1")
    assert f.status == "queued"
    assert f.failed_stage is None
    assert A.query(BatchImport).get("b1").failed == 0
