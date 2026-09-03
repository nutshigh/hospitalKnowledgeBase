import json
import logging
import os
import re
import time
import zlib
import zipfile
import tarfile
from typing import Optional

from sqlalchemy import text

from app.config import settings
from app.core import hospital_resolver
from app.core.database import get_hospital_db, get_template_db
from app.core.logging_config import setup_logging
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.core.retry import backoff_for_retry
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService

_log = logging.getLogger("app.batch.extract")

# 不含 docx(Spec F8:DOCX 从批量上传白名单移除)
ALLOWED_EXTS = {"pdf", "doc", "jpg", "jpeg", "png"}

# 文件名约定: <姓名>_<身份证后六位>.<ext>  (后六位 = 5 位数字 + 末位数字或 X)
_FILENAME_RE = re.compile(r"^([^_]+)_([0-9]{5}[0-9X])$")


def _parse_filename(filename: str) -> Optional[tuple[str, str]]:
    """从 zip/tar 内文件名抽取 (姓名, 身份证后六位)。
    Returns (str, str) on match, None on mismatch。
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    m = _FILENAME_RE.match(base)
    if m:
        return m.group(1), m.group(2)
    return None


# 批内缓存:batch_id → {(name, id_suffix): hospital_id | None};批结束清理
_batch_resolver_cache: dict[str, dict[tuple[str, str], Optional[str]]] = {}


def _resolve_hospital_id(batch_id, name, id_suffix) -> Optional[str]:
    cache = _batch_resolver_cache.setdefault(batch_id, {})
    key = (name, id_suffix)
    if key in cache:
        return cache[key]
    hospital_id = hospital_resolver.resolve_hospital(name, id_suffix)
    if hospital_id is None:
        cache[key] = None
        return None
    if not _hospital_registered(hospital_id):
        _log.warning("resolve hospital not registered batch=%s name=%s suffix=%s hid=%s",
                     batch_id, name, id_suffix, hospital_id)
        cache[key] = None
        return None
    cache[key] = hospital_id
    return hospital_id


def _hospital_registered(hospital_id: str) -> bool:
    """template 库 hospital_tenant 是否登记该医院且启用。"""
    db = next(get_template_db())
    try:
        row = db.execute(
            text("SELECT 1 FROM hospital_tenant WHERE hospital_id = :hid AND is_active = 1"),
            {"hid": hospital_id},
        ).fetchone()
        return row is not None
    finally:
        db.close()


def _record_hospital_not_found(db, batch_id, file_path, size):
    """记一行 file failed='hospital_not_found',既不落盘也不投 parsing。

    与 dispatch_unmatched 同等级短路:外部接口无匹配或解析出的医院本地未注册。
    """
    _log.info(
        "extract stage=hospital_not_found batch=%s file=%s size=%d",
        batch_id, file_path, size,
    )
    import uuid as _uuid
    fid = _uuid.uuid4().hex
    db.add(BatchImportFile(
        id=fid, batch_id=batch_id, file_path=file_path, file_size=size,
        crc32=f"hnf{_uuid.uuid4().hex[:5]}",
        status="failed", failed_stage="hospital_not_found",
        error_message="hospital_not_found",
    ))
    b = db.query(BatchImport).get(batch_id)
    if b is not None:
        b.failed = (b.failed or 0) + 1
    db.commit()


def handle_extract_task(message: dict):
    payload = message.get("payload", {})
    batch_id = payload.get("batch_id")
    hospital_id = message.get("hospital_id") or payload.get("hospital_id")
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
        _batch_resolver_cache.pop(batch_id, None)
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
                parsed = _parse_filename(info.filename)
                if parsed is None:
                    _record_dispatch_unmatched(db, b.id, info.filename, info.file_size)
                    continue
                name, id_suffix = parsed
                file_hospital = _resolve_hospital_id(b.id, name, id_suffix)
                if file_hospital is None:
                    _record_hospital_not_found(db, b.id, info.filename, info.file_size)
                    continue
                file_db = next(get_hospital_db(file_hospital)) if file_hospital != hospital_id else db
                try:
                    with zf.open(info) as fh:
                        _stream_to_report(file_db, b, file_hospital, info.filename, fh,
                                          info.file_size, name, id_suffix, batch_db=db)
                finally:
                    if file_db is not db:
                        file_db.close()
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
                parsed = _parse_filename(member.name)
                if parsed is None:
                    _record_dispatch_unmatched(db, b.id, member.name, member.size)
                    continue
                name, id_suffix = parsed
                file_hospital = _resolve_hospital_id(b.id, name, id_suffix)
                if file_hospital is None:
                    _record_hospital_not_found(db, b.id, member.name, member.size)
                    continue
                file_db = next(get_hospital_db(file_hospital)) if file_hospital != hospital_id else db
                try:
                    fh = tf.extractfile(member)
                    _stream_to_report(file_db, b, file_hospital, member.name, fh,
                                      member.size, name, id_suffix, batch_db=db)
                finally:
                    if file_db is not db:
                        file_db.close()


def _record_oversize(db, batch_id, file_path, size):
    """记一行 failed='oversize' 但不投 parsing(F6)。"""
    _log.info(
        "extract stage=oversize batch=%s file=%s size=%d",
        batch_id, file_path, size,
    )
    fid = BatchService.handle_extracted_file(db, batch_id, file_path,
                                              f"o{size:07x}", size)
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
    _log.info(
        "extract stage=dispatch_unmatched batch=%s file=%s size=%d",
        batch_id, file_path, size,
    )
    import uuid as _uuid
    fid = _uuid.uuid4().hex
    db.add(BatchImportFile(
        id=fid, batch_id=batch_id, file_path=file_path, file_size=size,
        crc32=f"un{_uuid.uuid4().hex[:6]}",
        status="failed", failed_stage=reason, error_message=reason,
    ))
    b = db.query(BatchImport).get(batch_id)
    if b is not None:
        b.failed = (b.failed or 0) + 1
    db.commit()


def _stream_to_report(target_db, b, hospital_id, rel_path, fh, size, name, user_id: str,
                      batch_db=None):
    """读流,算 crc32,落盘,建 report_task 并 publish parsing.bulk(幂等)。

    target_db: 报告归属医院(文件名 _医院编码_) 的 DB 会话(ReportTask 写至此)
    batch_db:   批次所在医院的 DB 会话(BatchImportFile 操作);默认同 target_db
    """
    if batch_db is None:
        batch_db = target_db
    data = fh.read()
    crc = f"{zlib.crc32(data) & 0xffffffff:08x}"
    fid = BatchService.handle_extracted_file(batch_db, b.id, rel_path, crc, size)
    f = batch_db.query(BatchImportFile).get(fid)
    if f is None:
        return
    if f.report_task_id is not None:
        return  # 已发布(幂等命中,F18 补差),不再 publish
    if not f.dispatch_hospital:
        # 记录本文件分发的目标医院(跨院分发时 worker/retry 需要定位任务所在库)
        f.dispatch_hospital = hospital_id
    _log.info(
        "extract stage=queued batch=%s file=%s file_id=%s user=%s size=%d target_hospital=%s",
        b.id, rel_path, fid, user_id, size, hospital_id,
    )
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
        db=target_db, hospital_id=hospital_id,
        user_id=user_id, name=name,
        file_path=disk_path, filename=os.path.basename(rel_path),
        file_type=file_type, file_size=size, priority="bulk",
        batch_id=b.id, file_id=fid,
        batch_hospital_id=b.hospital_id,
    )
    f.report_task_id = task.id
    batch_db.commit()


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