import os
import uuid
import zlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.modules.report.batch_models import BatchImport, BatchImportFile


class BatchService:

    @staticmethod
    def create_batch(db: Session, hospital_id: str, user_id: str, filename: str) -> BatchImport:
        bid = uuid.uuid4().hex
        storage_dir = os.path.join(settings.FILE_STORAGE_ROOT, hospital_id, "batch")
        os.makedirs(storage_dir, exist_ok=True)
        archive_path = os.path.join(storage_dir, f"{bid}.zip")
        b = BatchImport(
            id=bid, hospital_id=hospital_id, user_id=user_id,
            filename=filename, archive_path=archive_path, status="uploading",
        )
        db.add(b); db.commit(); db.refresh(b)
        return b

    @staticmethod
    def append_chunk(db: Session, batch_id: str, index: int, total: int,
                     chunk: bytes) -> int:
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            raise ValueError("batch not found")
        if b.status != "uploading":
            raise ValueError(f"batch not uploading (status={b.status})")
        part_dir = os.path.dirname(b.archive_path)
        part_path = os.path.join(part_dir, f"{batch_id}.part{index}")
        with open(part_path, "wb") as f:
            f.write(chunk)
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        return os.path.getsize(part_path)

    @staticmethod
    def finalize_batch(db: Session, batch_id: str, expected_crc32: Optional[str],
                       expected_total: int, expected_size: int) -> None:
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            raise ValueError("batch not found")
        part_dir = os.path.dirname(b.archive_path)

        def _cleanup_parts():
            """删除所有已上传的 .partN 分片 (失败/拒收路径;spec F1 分片清理)。"""
            if not os.path.isdir(part_dir):
                return
            for fn in os.listdir(part_dir):
                if fn.startswith(f"{batch_id}.part"):
                    try:
                        os.remove(os.path.join(part_dir, fn))
                    except OSError:
                        pass

        if expected_size > settings.BATCH_ARCHIVE_MAX_SIZE:
            b.status = "cancelled"
            b.error_message = "archive_too_large"
            db.commit()
            _cleanup_parts()
            raise ValueError("archive_too_large")
        got_indices = sorted(
            int(fn.split(".part")[-1])
            for fn in os.listdir(part_dir)
            if fn.startswith(f"{batch_id}.part")
        )
        if got_indices != list(range(expected_total)):
            b.status = "cancelled"
            b.error_message = "chunks_incomplete"
            db.commit()
            _cleanup_parts()
            raise ValueError("chunks_incomplete")
        with open(b.archive_path, "wb") as out:
            crc = 0
            for i in got_indices:
                with open(os.path.join(part_dir, f"{batch_id}.part{i}"), "rb") as part:
                    data = part.read()
                    out.write(data)
                    crc = zlib.crc32(data, crc)
            crc_hex = f"{crc & 0xffffffff:08x}"
        if expected_crc32 and crc_hex != expected_crc32.lower():
            b.status = "cancelled"
            b.error_message = "crc_mismatch"
            db.commit()
            _cleanup_parts()
            raise ValueError("crc_mismatch")
        for i in got_indices:
            try:
                os.remove(os.path.join(part_dir, f"{batch_id}.part{i}"))
            except OSError:
                pass
        b.status = "extracting"
        db.commit()
        BatchService.publish_extract_task(batch_id, b.hospital_id, b.archive_path)

    @staticmethod
    def publish_extract_task(batch_id: str, hospital_id: str, archive_path: str):
        rabbitmq.publish(TaskMessage(
            task_type="extract", hospital_id=hospital_id, priority="bulk",
            payload={"batch_id": batch_id, "archive_path": archive_path},
        ))

    @staticmethod
    def handle_extracted_file(db: Session, batch_id: str, file_path: str,
                               crc32: str, file_size: int) -> str:
        """幂等去重:同 (batch_id,crc32) 返回已存在 file_id,不重复记账。"""
        # FUTURE: global dedupe by crc32 —— 跨批查 batch_import_file 全表 (crc32+file_size)
        # 命中则复用旧 report_task_id 短路(OOC spec §3.4 故意不做,改 future)。
        existing = db.query(BatchImportFile).filter_by(
            batch_id=batch_id, crc32=crc32,
        ).first()
        if existing:
            return existing.id
        fid = uuid.uuid4().hex
        db.add(BatchImportFile(
            id=fid, batch_id=batch_id, file_path=file_path,
            file_size=file_size, crc32=crc32, status="queued",
        ))
        db.commit()
        return fid

    # 字段相关的合法前态守门。key=进度字段, value=该字段已计数过的终态集合
    # (这些态不应再被本字段推进,以此实现幂等 + 顺向状态机):
    #   parsed_ok 仅 queued → parsed
    #   interp_ok queued|parsed → interp_ok
    #   failed    queued|parsed|interp_ok → failed
    _PRIOR_OK = {
        "parsed_ok": ("parsed", "interp_ok", "failed"),
        "interp_ok": ("interp_ok", "failed"),
        "failed": ("failed",),
    }

    @staticmethod
    def increment_progress(db: Session, batch_id: str, file_id: str, field: str,
                           stage: Optional[str] = None) -> None:
        """field ∈ {'parsed_ok','interp_ok','failed'}。幂等:靠 field 相关的 file.status 守门只 ++ 一次。

        stage:仅 field=='failed' 时有意义,记录失败发生在哪一阶段
              ("parsing"|"interpretation"|"oversize"),供 retry_failed 分流。"""
        f = db.query(BatchImportFile).get(file_id)
        if f is None:
            return
        new_state = {
            "parsed_ok": "parsed",
            "interp_ok": "interp_ok",
            "failed": "failed",
        }[field]
        update_values = {BatchImportFile.status: new_state}
        if field == "failed" and stage:
            update_values[BatchImportFile.failed_stage] = stage
        result = db.query(BatchImportFile).filter(
            BatchImportFile.id == file_id,
            BatchImportFile.status.notin_(BatchService._PRIOR_OK[field]),
        ).update(update_values)
        if result == 0:
            db.commit()
            return
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            db.commit()
            return
        setattr(b, field, (getattr(b, field) or 0) + 1)
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        BatchService._maybe_advance_status(db, b)

    @staticmethod
    def _maybe_advance_status(db: Session, b: BatchImport) -> None:
        if b.status in ("completed", "partial_failed", "cancelled"):
            return
        if b.total <= 0:
            return
        # Note: parsed_ok 统计的是"解析完成但解读尚未完成"的中间态文件;
        # 终态完成条件是 interp_ok + failed == total (解读是 batch 终态阶段)。
        terminal_done = (b.interp_ok or 0) + (b.failed or 0)
        if terminal_done < b.total:
            # 解读阶段一经产出第一个 interp_ok,自动由 parsing 推进到 interpreting
            if b.status == "parsing" and (b.interp_ok or 0) > 0:
                b.status = "interpreting"
                db.commit()
            return
        if (b.failed or 0) == 0:
            b.status = "completed"
        else:
            b.status = "partial_failed"
        b.completed_at = datetime.now(timezone.utc)
        db.commit()

    @staticmethod
    def get_progress(db: Session, batch_id: str) -> dict:
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            raise ValueError("batch not found")
        files = db.query(BatchImportFile).filter_by(batch_id=batch_id).all()
        failing = [f for f in files if f.status == "failed"]
        return {
            "batch": {
                "id": b.id, "filename": b.filename, "status": b.status,
                "total": b.total, "parsed_ok": b.parsed_ok,
                "interp_ok": b.interp_ok, "failed": b.failed,
                "error_message": b.error_message,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "completed_at": b.completed_at.isoformat() if b.completed_at else None,
            },
            "failing_files": [
                {"id": f.id, "file_path": f.file_path,
                 "failed_stage": f.failed_stage, "error_message": f.error_message}
                for f in failing
            ],
        }

    @staticmethod
    def retry_failed(db: Session, batch_id: str, file_ids: Optional[list] = None) -> dict:
        b = db.query(BatchImport).get(batch_id)
        if b is None or b.status == "cancelled":
            raise ValueError("batch not retryable")
        q = db.query(BatchImportFile).filter_by(batch_id=batch_id, status="failed")
        if file_ids:
            q = q.filter(BatchImportFile.id.in_(file_ids))
        files = q.all()
        requeued = 0
        skipped_unretryable = 0
        saw_interp = False
        UNRETRYABLE_STAGES = ("oversize", "dispatch_unmatched")
        for f in files:
            stage = f.failed_stage
            if stage in UNRETRYABLE_STAGES:
                # oversize / dispatch_unmatched 无 report_task_id,重试无意义;
                # 跳过且不重置状态,仅累计跳过数供前端提示。
                skipped_unretryable += 1
                continue
            f.status = "queued"
            f.error_message = None
            f.failed_stage = None
            if stage == "interpretation":
                # 解读失败:parse 仍 OK,只重置 ReportInterpretation 并重投 interpretation.bulk,
                # 不动 ReportTask.retry_count。
                report_id = BatchService._report_id_for_file(db, f)
                if report_id is not None:
                    BatchService._reset_interp_for_retry(db, report_id)
                    rabbitmq.publish(TaskMessage(
                        task_type="interpretation", hospital_id=b.hospital_id,
                        priority="bulk",
                        payload={"report_id": report_id, "hospital_id": b.hospital_id,
                                 "batch_id": batch_id, "file_id": f.id},
                    ))
                    saw_interp = True
            else:
                # parsing 或 oversize:有 report_task_id 才重投解析(oversize 一般无 task_id)。
                if f.report_task_id:
                    from app.modules.report.models import ReportTask
                    t = db.query(ReportTask).get(f.report_task_id)
                    if t:
                        t.status = "queued"
                        t.retry_count = 0
                        rabbitmq.publish(TaskMessage(
                            task_type="parsing", hospital_id=b.hospital_id,
                            priority="bulk",
                            payload={"task_id": t.id, "hospital_id": b.hospital_id,
                                     "file_path": t.original_file_path, "batch_id": batch_id,
                                     "file_id": f.id},
                        ))
            requeued += 1
        b.failed = max(0, (b.failed or 0) - requeued)
        if b.status == "partial_failed" and requeued > 0:
            # 解读失败重投应回到 interpreting 解读态,parsing/oversize 重投回到 parsing。
            b.status = "interpreting" if saw_interp else "parsing"
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        BatchService._maybe_advance_status(db, b)
        return {"requeued": requeued, "skipped_unretryable": skipped_unretryable}

    @staticmethod
    def _report_id_for_file(db: Session, f: BatchImportFile) -> Optional[int]:
        """由 file 关联的 report_task 推回 ReportInfo.id(report_id)。"""
        if not f.report_task_id:
            return None
        from app.modules.report.models import ReportInfo
        report = db.query(ReportInfo).filter(ReportInfo.task_id == f.report_task_id).first()
        return report.id if report else None

    @staticmethod
    def _reset_interp_for_retry(db: Session, report_id: int) -> None:
        """把某 report_id 下所有解读行重置为 pending、retry_count=0,允许重跑。"""
        from app.modules.interpretation.models import ReportInterpretation
        db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id
        ).update({ReportInterpretation.status: "pending",
                  ReportInterpretation.retry_count: 0})