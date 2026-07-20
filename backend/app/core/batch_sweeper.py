import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.core.database import get_hospital_db
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService

log = logging.getLogger("app.batch.sweeper")


# TODO: replace with a hospital registry when multi-tenant sweep is needed
def _hospital_ids():
    return ("H001",)


async def start():
    """后台协程,周期巡检卡住的 batch。"""
    while True:
        try:
            await asyncio.sleep(settings.BATCH_SWEEP_INTERVAL)
            _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("sweep error: %s", e)


def _sweep_once():
    stall_threshold = datetime.now(timezone.utc) - timedelta(
        seconds=settings.BATCH_SWEEP_STALL_THRESHOLD
    )
    chunk_timeout = datetime.now(timezone.utc) - timedelta(
        seconds=settings.BATCH_CHUNK_TIMEOUT
    )

    for hospital_id in _hospital_ids():
        try:
            db = next(get_hospital_db(hospital_id))
            try:
                _sweep_reaper(db, hospital_id, chunk_timeout)
                _sweep_stuck(db, hospital_id, stall_threshold)
            finally:
                db.close()
        except Exception:
            log.exception("sweep for hospital %s failed", hospital_id)


def _sweep_reaper(db, hospital_id: str, chunk_timeout) -> None:
    """F4 reaper: clean up orphan 'uploading' batches past chunk timeout."""
    stale = (
        db.query(BatchImport)
        .filter(
            BatchImport.status == "uploading",
            BatchImport.updated_at < chunk_timeout,
        )
        .all()
    )
    for b in stale:
        part_dir = os.path.dirname(b.archive_path)
        if part_dir and os.path.isdir(part_dir):
            for fn in os.listdir(part_dir):
                if fn.startswith(f"{b.id}.part"):
                    try:
                        os.remove(os.path.join(part_dir, fn))
                    except OSError:
                        pass
        db.query(BatchImportFile).filter_by(batch_id=b.id).delete()
        db.delete(b)
        db.commit()
        log.info("reaped orphan uploading batch %s", b.id)


def _sweep_stuck(db, hospital_id: str, stall_threshold) -> None:
    """Stuck extracting/parsing/interpreting: attempt advance; re-publish extract for stuck extracting."""
    stuck = (
        db.query(BatchImport)
        .filter(
            BatchImport.status.in_(("extracting", "parsing", "interpreting")),
            BatchImport.updated_at < stall_threshold,
        )
        .all()
    )
    for b in stuck:
        # cancelled batches are never advanced/republished (defensive double-check)
        if b.status == "cancelled":
            continue
        BatchService._maybe_advance_status(db, b)
        db.refresh(b)
        # extracting 卡住 → 触发 extract 续跑(幂等)
        if (
            b.status == "extracting"
            and b.updated_at is not None
            and b.updated_at.replace(tzinfo=timezone.utc) < stall_threshold
        ):
            try:
                BatchService.publish_extract_task(b.id, b.hospital_id, b.archive_path)
            except Exception:
                log.exception("republish extract for %s failed", b.id)