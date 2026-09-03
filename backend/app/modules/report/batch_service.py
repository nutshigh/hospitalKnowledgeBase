import logging
import os
import uuid
import zlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.modules.report.batch_models import BatchImport, BatchImportFile

_log = logging.getLogger("app.batch")


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
        _log.info("batch record created hospital=%s user=%s batch=%s path=%s name=%s",
                   hospital_id, user_id, bid, archive_path, (filename or "")[:200])
        return b

    @staticmethod
    def append_chunk(db: Session, batch_id: str, index: int, total: int,
                     chunk: bytes) -> int:
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            _log.warning("chunk refused batch=%s index=%d/%d reason=not_found",
                          batch_id, index, total)
            raise ValueError("batch not found")
        if b.status != "uploading":
            _log.warning("chunk refused batch=%s index=%d/%d status=%s",
                          batch_id, index, total, b.status)
            raise ValueError(f"batch not uploading (status={b.status})")
        part_dir = os.path.dirname(b.archive_path)
        part_path = os.path.join(part_dir, f"{batch_id}.part{index}")
        with open(part_path, "wb") as f:
            f.write(chunk)
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        written = os.path.getsize(part_path)
        _log.debug("chunk written batch=%s index=%d/%d bytes=%d path=%s",
                    batch_id, index, total, written, part_path)
        return written

    @staticmethod
    def finalize_batch(db: Session, batch_id: str, expected_crc32: Optional[str],
                       expected_total: int, expected_size: int) -> None:
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            _log.warning("finalize refused batch=%s reason=not_found", batch_id)
            raise ValueError("batch not found")
        part_dir = os.path.dirname(b.archive_path)

        def _cleanup_parts():
            if not os.path.isdir(part_dir):
                return
            for fn in os.listdir(part_dir):
                if fn.startswith(f"{batch_id}.part"):
                    try:
                        os.remove(os.path.join(part_dir, fn))
                    except OSError:
                        pass

        if expected_size > settings.BATCH_ARCHIVE_MAX_SIZE:
            _log.warning("finalize failed batch=%s reason=archive_too_large size=%d max=%d",
                          batch_id, expected_size, settings.BATCH_ARCHIVE_MAX_SIZE)
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
            _log.warning("finalize failed batch=%s reason=chunks_incomplete expected=%d got=%s",
                          batch_id, expected_total, got_indices)
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
            _log.warning("finalize failed batch=%s reason=crc_mismatch expected=%s got=%s",
                          batch_id, expected_crc32, crc_hex)
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
        _log.info("finalize ok batch=%s chunks=%d size=%d crc=%s",
                   batch_id, expected_total, expected_size, crc_hex)
        BatchService.publish_extract_task(batch_id, b.hospital_id, b.archive_path)

    @staticmethod
    def publish_extract_task(batch_id: str, hospital_id: str, archive_path: str):
        _log.info("extract task published batch=%s hospital=%s path=%s",
                   batch_id, hospital_id, archive_path)
        rabbitmq.publish(TaskMessage(
            task_type="extract", hospital_id=hospital_id, priority="bulk",
            payload={"batch_id": batch_id, "archive_path": archive_path},
        ))

    @staticmethod
    def handle_extracted_file(db: Session, batch_id: str, file_path: str,
                               crc32: str, file_size: int) -> str:
        existing = db.query(BatchImportFile).filter_by(
            batch_id=batch_id, crc32=crc32,
        ).first()
        if existing:
            _log.debug("extract dedup batch=%s crc=%s path=%s -> existing=%s",
                        batch_id, crc32, file_path, existing.id)
            return existing.id
        fid = uuid.uuid4().hex
        db.add(BatchImportFile(
            id=fid, batch_id=batch_id, file_path=file_path,
            file_size=file_size, crc32=crc32, status="queued",
        ))
        db.commit()
        _log.info("extract file registered batch=%s fid=%s path=%s size=%d crc=%s",
                   batch_id, fid, file_path, file_size, crc32)
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
        f = db.query(BatchImportFile).get(file_id)
        if f is None:
            _log.warning("increment_progress skipped batch=%s fid=%s field=%s reason=file_not_found",
                          batch_id, file_id, field)
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
            _log.debug("increment_progress idempotent batch=%s fid=%s field=%s status=%s",
                        batch_id, file_id, field, f.status)
            return
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            db.commit()
            _log.warning("increment_progress batch_not_found batch=%s fid=%s field=%s",
                          batch_id, file_id, field)
            return
        before = getattr(b, field) or 0
        setattr(b, field, before + 1)
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        _log.info("increment_progress batch=%s fid=%s field=%s stage=%s count=%d->%d",
                   batch_id, file_id, field, stage or "-", before, before + 1)
        BatchService._maybe_advance_status(db, b)

    @staticmethod
    def update_batch_progress(batch_hospital_id: Optional[str],
                              fallback_hospital_id: Optional[str],
                              fallback_db: Session, batch_id: str, file_id: str,
                              field: str, stage: Optional[str] = None) -> None:
        """在“批次所属库”记 file 进度(跨院分发时 batch_hospital_id 是上传方医院)。

        worker 打开的是任务所在的目标医院库,而 BatchImportFile/BatchImport 行存在
        上传方(批次)库中;两者一致时用 fallback_db,不一致时另开批次库会话。
        """
        if batch_hospital_id and batch_hospital_id != fallback_hospital_id:
            batch_db = next(get_hospital_db(batch_hospital_id))
            try:
                BatchService._increment(batch_db, batch_id, file_id, field, stage)
            finally:
                batch_db.close()
            return
        BatchService._increment(fallback_db, batch_id, file_id, field, stage)

    @staticmethod
    def _increment(db: Session, batch_id: str, file_id: str, field: str,
                   stage: Optional[str] = None) -> None:
        if stage is not None:
            BatchService.increment_progress(db, batch_id, file_id, field, stage=stage)
        else:
            BatchService.increment_progress(db, batch_id, file_id, field)

    @staticmethod
    def _maybe_advance_status(db: Session, b: BatchImport) -> None:
        old = b.status
        if old in ("completed", "partial_failed", "cancelled"):
            return
        if b.total <= 0:
            return
        terminal_done = (b.interp_ok or 0) + (b.failed or 0)
        if terminal_done < b.total:
            if b.status == "parsing" and (b.interp_ok or 0) > 0:
                b.status = "interpreting"
                db.commit()
                _log.info("status_advance batch=%s %s -> interpreting (first interp_ok)",
                           b.id, old)
            return
        if (b.failed or 0) == 0:
            b.status = "completed"
        else:
            b.status = "partial_failed"
        b.completed_at = datetime.now(timezone.utc)
        db.commit()
        _log.info("status_advance batch=%s %s -> %s total=%d ok=%d failed=%d",
                   b.id, old, b.status, b.total, b.interp_ok or 0, b.failed or 0)

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
            _log.warning("retry refused batch=%s reason=not_retryable", batch_id)
            raise ValueError("batch not retryable")
        q = db.query(BatchImportFile).filter_by(batch_id=batch_id, status="failed")
        if file_ids:
            q = q.filter(BatchImportFile.id.in_(file_ids))
        files = q.all()
        requeued = 0
        skipped_unretryable = 0
        saw_interp = False
        UNRETRYABLE_STAGES = ("oversize", "dispatch_unmatched", "hospital_not_found")
        for f in files:
            stage = f.failed_stage
            if stage in UNRETRYABLE_STAGES:
                skipped_unretryable += 1
                _log.info("retry skip unretryable batch=%s fid=%s stage=%s",
                          batch_id, f.id, stage)
                continue
            f.status = "queued"
            f.error_message = None
            f.failed_stage = None
            # 任务/report 位于该文件分发的目标医院库(跨院分发时与批次库不同)
            target_hospital = f.dispatch_hospital or b.hospital_id
            need_sep = target_hospital != b.hospital_id
            tdb = next(get_hospital_db(target_hospital)) if need_sep else db
            try:
                if stage == "interpretation":
                    report_id = BatchService._report_id_for_file(tdb, f)
                    if report_id is not None:
                        BatchService._reset_interp_for_retry(tdb, report_id)
                        rabbitmq.publish(TaskMessage(
                            task_type="interpretation", hospital_id=target_hospital,
                            priority="bulk",
                            payload={"report_id": report_id, "hospital_id": target_hospital,
                                     "batch_id": batch_id, "file_id": f.id,
                                     "batch_hospital_id": b.hospital_id},
                        ))
                        saw_interp = True
                        _log.info("retry requeue interp batch=%s fid=%s report_id=%s target=%s",
                                  batch_id, f.id, report_id, target_hospital)
                else:
                    if f.report_task_id:
                        from app.modules.report.models import ReportTask
                        t = tdb.query(ReportTask).get(f.report_task_id)
                        if t:
                            t.status = "queued"
                            t.retry_count = 0
                            rabbitmq.publish(TaskMessage(
                                task_type="parsing", hospital_id=target_hospital,
                                priority="bulk",
                                payload={"task_id": t.id, "hospital_id": target_hospital,
                                         "file_path": t.original_file_path,
                                         "batch_id": batch_id, "file_id": f.id,
                                         "batch_hospital_id": b.hospital_id},
                            ))
                            _log.info("retry requeue parsing batch=%s fid=%s task_id=%s target=%s",
                                      batch_id, f.id, t.id, target_hospital)
            finally:
                if need_sep:
                    tdb.commit()
                    tdb.close()
            requeued += 1
        b.failed = max(0, (b.failed or 0) - requeued)
        if b.status == "partial_failed" and requeued > 0:
            b.status = "interpreting" if saw_interp else "parsing"
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        _log.info("retry done batch=%s requeued=%d skipped=%d new_status=%s",
                   batch_id, requeued, skipped_unretryable, b.status)
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