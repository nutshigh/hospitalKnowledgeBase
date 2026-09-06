from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional
import os
import uuid

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.knowledge import schemas, service
from app.config import settings

router = APIRouter()


def _get_db(
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@router.get("/categories", response_model=list[schemas.CategoryResponse])
def list_categories(db: Session = Depends(_get_db)):
    return service.list_categories(db)


@router.post("/categories", response_model=schemas.CategoryResponse)
def create_category(data: schemas.CategoryCreate, db: Session = Depends(_get_db)):
    return service.create_category(db, data.name, data.parent_id, data.sort_order)


@router.put("/categories/{category_id}", response_model=schemas.CategoryResponse)
def update_category(category_id: int, data: schemas.CategoryUpdate, db: Session = Depends(_get_db)):
    cat = service.update_category(db, category_id, data.name, data.parent_id, data.sort_order)
    if not cat:
        raise NotFoundException(detail="Category not found")
    return cat


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, db: Session = Depends(_get_db)):
    if not service.delete_category(db, category_id):
        raise NotFoundException(detail="Category not found")
    return {"status": "deleted"}


@router.get("/entries", response_model=schemas.EntryListResponse)
def list_entries(
    category_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(_get_db),
):
    items, total = service.list_entries(db, category_id, page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/entries", response_model=schemas.EntryResponse)
def create_entry(
    data: schemas.EntryCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(_get_db),
):
    return service.create_entry(db, current_user.hospital_id, data.title, data.content, data.category_id)


@router.get("/entries/{entry_id}", response_model=schemas.EntryResponse)
def get_entry(entry_id: int, db: Session = Depends(_get_db)):
    entry = service.get_entry(db, entry_id)
    if not entry:
        raise NotFoundException(detail="Entry not found")
    return entry


@router.put("/entries/{entry_id}", response_model=schemas.EntryResponse)
def update_entry(
    entry_id: int,
    data: schemas.EntryUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(_get_db),
):
    entry = service.update_entry(db, current_user.hospital_id, entry_id, data.title, data.content, data.category_id)
    if not entry:
        raise NotFoundException(detail="Entry not found")
    return entry


@router.delete("/entries/{entry_id}")
def delete_entry(
    entry_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(_get_db),
):
    if not service.delete_entry(db, current_user.hospital_id, entry_id):
        raise NotFoundException(detail="Entry not found")
    return {"status": "deleted"}


@router.post("/import")
def import_document(
    file: UploadFile = File(...),
    category_id: Optional[int] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(_get_db),
):
    allowed_exts = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md"}
    _, ext = os.path.splitext(file.filename)
    if ext.lower() not in allowed_exts:
        raise ValidationException(detail=f"Unsupported format. Allowed: {allowed_exts}")

    storage_dir = os.path.join(settings.FILE_STORAGE_ROOT, current_user.hospital_id, "knowledge")
    os.makedirs(storage_dir, exist_ok=True)
    tmp_path = os.path.join(storage_dir, f"{uuid.uuid4().hex}{ext}")
    with open(tmp_path, "wb") as f:
        f.write(file.file.read())

    try:
        count = service.import_from_file(db, current_user.hospital_id, tmp_path, file.filename, category_id)
        return {"imported": count, "filename": file.filename}
    except Exception as e:
        raise ValidationException(detail=f"Import failed: {str(e)}")


@router.post("/reindex/{category_id}")
def reindex_category(
    category_id: int,
    current_user: CurrentUser = Depends(get_current_user),
):
    service.reindex_category(current_user.hospital_id, category_id)
    return {"status": "reindexed", "category_id": category_id}
