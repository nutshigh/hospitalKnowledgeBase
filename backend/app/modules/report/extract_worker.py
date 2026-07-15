import os
import time
import zlib
import zipfile
import tarfile

from app.config import settings
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService

# 不含 docx(Spec F8:DOCX 从批量上传白名单移除)
ALLOWED_EXTS = {"pdf", "doc", "jpg", "jpeg", "png"}


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
                    continue  # zip 炸弹防护(F6)
                with zf.open(info) as fh:
                    _stream_to_report(db, b, hospital_id, info.filename, fh, info.file_size)
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
                fh = tf.extractfile(member)
                _stream_to_report(db, b, hospital_id, member.name, fh, member.size)


def _record_oversize(db, batch_id, file_path, size):
    """记一行 failed='oversize' 但不投 parsing(F6)。"""
    fid = BatchService.handle_extracted_file(db, batch_id, file_path,
                                              f"ovs{size:08x}", size)
    f = db.query(BatchImportFile).get(fid)
    if f and f.status == "queued":
        f.status = "failed"
        f.error_message = "oversize"
        b = db.query(BatchImport).get(batch_id)
        if b is not None:
            b.failed = (b.failed or 0) + 1
        db.commit()


def _stream_to_report(db, b, hospital_id, rel_path, fh, size):
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
        user_id=int(b.user_id) if str(b.user_id).isdigit() else 0,
        file_path=disk_path, filename=os.path.basename(rel_path),
        file_type=file_type, file_size=size, priority="bulk",
        batch_id=b.id, file_id=fid,
    )
    f.report_task_id = task.id
    db.commit()


def start_worker():
    while True:
        try:
            rabbitmq.consume("extract.bulk", handle_extract_task, prefetch_count=1)
            print("Extract worker started")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Extract worker disconnected: {e}, reconnect in 3s")
            time.sleep(3)