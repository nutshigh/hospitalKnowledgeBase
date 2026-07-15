import os
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.core.rabbitmq import rabbitmq
from app.modules.report.batch_models import BatchImport
from app.modules.report.batch_service import BatchService


router = APIRouter()


def _db(current_user: CurrentUser = Depends(get_current_user)):
    if not current_user.hospital_id:
        raise HTTPException(400, "Hospital context required")
    if current_user.role != "admin":
        raise HTTPException(403, "admin only")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@router.post("/batches")
def create_batch(filename: str = Form(...),
                 db: Session = Depends(_db),
                 user: CurrentUser = Depends(get_current_user)):
    b = BatchService.create_batch(db, user.hospital_id, str(user.user_id), filename)
    return {"batch_id": b.id}


@router.post("/batches/{batch_id}/chunk")
async def upload_chunk(batch_id: str,
                       index: int = Form(...),
                       total: int = Form(...),
                       data: UploadFile = File(...),
                       db: Session = Depends(_db)):
    chunk = await data.read()
    try:
        BatchService.append_chunk(db, batch_id, index, total, chunk)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"received": index, "total": total}


@router.post("/batches/{batch_id}/complete")
def complete_batch(batch_id: str, body: dict,
                   db: Session = Depends(_db)):
    try:
        expected_total = int(body["expected_total"])
        expected_size = int(body["expected_size"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "expected_total and expected_size required")
    expected_crc32 = body.get("expected_crc32")
    try:
        BatchService.finalize_batch(
            db, batch_id, expected_crc32, expected_total, expected_size,
        )
    except ValueError as e:
        code = str(e)
        if code in ("archive_too_large", "crc_mismatch", "chunks_incomplete"):
            raise HTTPException(400, detail=code)
        raise HTTPException(400, detail=code)
    return {"batch_id": batch_id, "status": "extracting"}


@router.get("/batches")
def list_batches(page: int = Query(1, ge=1),
                 page_size: int = Query(20, ge=1, le=100),
                 status: Optional[str] = None,
                 db: Session = Depends(_db),
                 user: CurrentUser = Depends(get_current_user)):
    q = db.query(BatchImport).filter_by(hospital_id=user.hospital_id)
    if status:
        q = q.filter(BatchImport.status == status)
    total = q.count()
    items = (q.order_by(BatchImport.created_at.desc())
              .offset((page - 1) * page_size).limit(page_size).all())
    return {
        "items": [{
            "id": b.id, "filename": b.filename, "status": b.status,
            "total": b.total, "parsed_ok": b.parsed_ok, "interp_ok": b.interp_ok,
            "failed": b.failed,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        } for b in items],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(_db)):
    try:
        return BatchService.get_progress(db, batch_id)
    except ValueError:
        raise HTTPException(404, "batch not found")


@router.get("/batches/{batch_id}/dead")
def get_dead(batch_id: str, db: Session = Depends(_db)):
    return {"dead": rabbitmq.consume_dead(batch_id)}


@router.post("/batches/{batch_id}/retry")
def retry_batch(batch_id: str, body: dict = {},
                db: Session = Depends(_db)):
    try:
        return BatchService.retry_failed(db, batch_id, body.get("file_ids"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, db: Session = Depends(_db)):
    b = db.query(BatchImport).get(batch_id)
    if b is None:
        raise HTTPException(404, "batch not found")
    if b.status in ("completed", "partial_failed"):
        raise HTTPException(400, f"cannot cancel batch in status={b.status}")
    b.status = "cancelled"
    db.commit()
    return {"cancelled": True}