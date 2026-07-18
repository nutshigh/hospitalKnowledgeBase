import json
import logging
import os
import re
import time
import zlib
import zipfile
import tarfile
from typing import Optional

from app.config import settings
from app.core.database import get_hospital_db
from app.core.logging_config import setup_logging
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.core.retry import backoff_for_retry
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService

_log = logging.getLogger("app.batch.extract")

# 不含 docx(Spec F8:DOCX 从批量上传白名单移除)
ALLOWED_EXTS = {"pdf", "doc", "jpg", "jpeg", "png"}

# 文件名约定: <姓名>_<医院编号>_<用户编号>.<ext>
# basename 去扩展后,3 段半角下划线分隔,末段纯数字 = user_id。
_FILENAME_RE = re.compile(r"^([^_]+)_([^_]+)_(\d+)$")


def _resolve_user_id(filename: str) -> Optional[int]:
    """从 zip/tar 内文件名抽取终端用户 user_id。
    Returns int user_id on match, None on mismatch。
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    m = _FILENAME_RE.match(base)
    return int(m.group(3)) if m else None


def handle_extract_task(message: dict):
    payload = message.get("payload", {})
    batch_id = payload.get("batch_id")
    hospital_id = payload.get("hospital_id")
    archive_path = payload.get("archive_path")

    db = next(get_hospital_db(hospital_id))
    try:
        b = db.query(BatchImport).get(batch_id)
        if b is None or b.status == "cancelled":
            return  # 已取消,ack 跳过(F5/补差保护)
        try:
            _extract_and_enqueue(db, b, hospital_id, archive_path)
        except (zipfile.BadZipFile, tarfile.TarError, EOFError) as e:
            b.status = "partial_failed"
            b.error_message = f"archive_corrupt: {e}"
            db.commit()
            return
        except Exception as e:
            # 瞬时异常(DB 抖动 / publish 失败 / OS 错等):走延迟队列重试,不直接 DLQ。
            _log.warning("extract transient failure for batch %s: %s", batch_id, e)
            retry_count = payload.get("retry_count", 0) + 1
            if retry_count >= 3:
                b = db.query(BatchImport).get(batch_id)
                b.status = "partial_failed"
                b.error_message = f"extract_failed_after_retries: {e}"
                db.commit()
                return  # 重试耗尽,ack 当前消息(batch 已置 partial_failed 终态)
            body = json.dumps({
                "task_type": "extract",
                "hospital_id": hospital_id,
                "payload": {**payload, "retry_count": retry_count},
            }).encode()
            rabbitmq.publish_retry(
                "extract.bulk", body,
                backoff_for_retry(retry_count - 1),
                batch_id=batch_id,
            )
            return  # ack 当前消息;retry 队列已存延迟副本,batch 仍 extracting
        b = db.query(BatchImport).get(batch_id)
        total = db.query(BatchImportFile).filter_by(batch_id=batch_id).count()
        b.total = total
        if total == 0:
            b.status = "partial_failed"
            b.error_message = "no_valid_files"
        else:
            b.status = "parsing"
        db.commit()
        # 若全部 file 已 failed(如全 oversize),推进到 partial_failed(F6)
        BatchService._maybe_advance_status(db, b)
    finally:
        db.close()


def _extract_and_enqueue(db, b, hospital_id, archive_path):
    archive_size = os.path.getsize(archive_path)
    cum_uncompressed = 0
    if archive_path.endswith((".zip", ".ZIP")):
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = os.path.basename(info.filename)
                if name.startswith(".") or "__MACOSX" in info.filename:
                    continue
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                if ext not in ALLOWED_EXTS:
                    continue  # F7:未识别扩展名,跳过
                if info.file_size > settings.BATCH_FILE_MAX_SIZE:
                    _record_oversize(db, b.id, info.filename, info.file_size)
                    continue
                cum_uncompressed += info.file_size
                if cum_uncompressed > 5 * archive_size:
                    _record_oversize(db, b.id, info.filename, info.file_size)
                    continue
                user_id = _resolve_user_id(info.filename)
                if user_id is None:
                    _record_dispatch_unmatched(db, b.id, info.filename, info.file_size)
                    continue
                with zf.open(info) as fh:
                    _stream_to_report(db, b, hospital_id, info.filename, fh,
                                      info.file_size, user_id)
    else:
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = os.path.basename(member.name)
                if "__MACOSX" in member.name or name.startswith("."):
                    continue
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                if ext not in ALLOWED_EXTS:
                    continue
                if member.size > settings.BATCH_FILE_MAX_SIZE:
                    _record_oversize(db, b.id, member.name, member.size)
                    continue
                cum_uncompressed += member.size
                if cum_uncompressed > 5 * archive_size:
                    _record_oversize(db, b.id, member.name, member.size)
                    continue
                user_id = _resolve_user_id(member.name)
                if user_id is None:
                    _record_dispatch_unmatched(db, b.id, member.name, member.size)
                    continue
                fh = tf.extractfile(member)
                _stream_to_report(db, b, hospital_id, member.name, fh, member.size, user_id)


def _record_oversize(db, batch_id, file_path, size):
    """记一行 failed='oversize' 但不投 parsing(F6)。"""
    fid = BatchService.handle_extracted_file(db, batch_id, file_path,
                                              f"ovs{size:08x}", size)
    f = db.query(BatchImportFile).get(fid)
    if f and f.status == "queued":
        f.status = "failed"
        f.failed_stage = "oversize"
        f.error_message = "oversize"
        b = db.query(BatchImport).get(batch_id)
        if b is not None:
            b.failed = (b.failed or 0) + 1
        db.commit()


def _record_dispatch_unmatched(db, batch_id, file_path, size,
                                reason="dispatch_unmatched"):
    """记一行 file failed,既不落盘也不投 parsing(与 oversize 同等级短路)。

    直接新建一行 BatchImportFile(uuid 主键,占位唯一 crc 不参与批内去重),
    复用 handle_extracted_file 会因占位 crc 把两个同 size unmatched 文件误去重。
    """
    import uuid as _uuid
    fid = _uuid.uuid4().hex
    db.add(BatchImportFile(
        id=fid, batch_id=batch_id, file_path=file_path, file_size=size,
        crc32=f"unm{_uuid.uuid4().hex[:8]}",
        status="failed", failed_stage=reason, error_message=reason,
    ))
    b = db.query(BatchImport).get(batch_id)
    if b is not None:
        b.failed = (b.failed or 0) + 1
    db.commit()


def _stream_to_report(db, b, hospital_id, rel_path, fh, size, user_id: int):
    """读流,算 crc32,落盘,建 report_task 并 publish parsing.bulk(幂等)。"""
    data = fh.read()
    crc = f"{zlib.crc32(data) & 0xffffffff:08x}"
    fid = BatchService.handle_extracted_file(db, b.id, rel_path, crc, size)
    f = db.query(BatchImportFile).get(fid)
    if f is None:
        return
    if f.report_task_id is not None:
        return  # 已发布(幂等命中,F18 补差),不再 publish
    # 落盘至 FILE_STORAGE_ROOT/<h>/batch/extracted/<batch_id>/<fid>.<ext>
    ext = os.path.splitext(rel_path)[1].lstrip(".")
    extract_dir = os.path.join(os.path.dirname(b.archive_path), "extracted", b.id)
    os.makedirs(extract_dir, exist_ok=True)
    disk_path = os.path.join(extract_dir, f"{fid}.{ext}")
    with open(disk_path, "wb") as out:
        out.write(data)
    file_type = {"pdf": "pdf", "doc": "docx", "jpg": "image",
                 "jpeg": "image", "png": "image"}.get(ext, "image")
    from app.modules.report.service import create_task
    task = create_task(
        db=db, hospital_id=hospital_id,
        user_id=user_id,   # ← 文件名解析出的目标终端用户
        file_path=disk_path, filename=os.path.basename(rel_path),
        file_type=file_type, file_size=size, priority="bulk",
        batch_id=b.id, file_id=fid,
    )
    f.report_task_id = task.id
    db.commit()


def start_worker():
    setup_logging()
    while True:
        try:
            rabbitmq.consume("extract.bulk", handle_extract_task, prefetch_count=1)
            print("Extract worker started")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Extract worker disconnected: {e}, reconnect in 3s")
            time.sleep(3)