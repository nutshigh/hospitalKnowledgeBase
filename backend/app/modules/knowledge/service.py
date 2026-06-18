from typing import List, Optional
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.modules.knowledge.models import KnowledgeCategory, KnowledgeEntry
from app.modules.knowledge.schemas import SearchResult
from app.ai import rag as ai_rag
from llama_index.core import Document


# ---- Category CRUD ----

def list_categories(db: Session) -> List[dict]:
    rows = db.query(KnowledgeCategory).order_by(KnowledgeCategory.sort_order).all()
    return [
        {"id": r.id, "name": r.name, "parent_id": r.parent_id, "sort_order": r.sort_order,
         "created_at": r.created_at, "updated_at": r.updated_at}
        for r in rows
    ]


def get_category(db: Session, category_id: int) -> Optional[KnowledgeCategory]:
    return db.query(KnowledgeCategory).filter(KnowledgeCategory.id == category_id).first()


def create_category(db: Session, name: str, parent_id: Optional[int] = None, sort_order: int = 0) -> KnowledgeCategory:
    cat = KnowledgeCategory(name=name, parent_id=parent_id, sort_order=sort_order)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def update_category(db: Session, category_id: int, name: Optional[str] = None,
                    parent_id: Optional[int] = None, sort_order: Optional[int] = None) -> Optional[KnowledgeCategory]:
    cat = get_category(db, category_id)
    if not cat:
        return None
    if name is not None:
        cat.name = name
    if parent_id is not None:
        cat.parent_id = parent_id
    if sort_order is not None:
        cat.sort_order = sort_order
    db.commit()
    db.refresh(cat)
    return cat


def delete_category(db: Session, category_id: int) -> bool:
    cat = get_category(db, category_id)
    if not cat:
        return False
    db.query(KnowledgeCategory).filter(KnowledgeCategory.parent_id == category_id).update({"parent_id": cat.parent_id})
    db.delete(cat)
    db.commit()
    return True


# ---- Entry CRUD ----

def list_entries(db: Session, category_id: Optional[int] = None,
                 page: int = 1, page_size: int = 20) -> tuple:
    q = db.query(KnowledgeEntry).filter(KnowledgeEntry.status == 1)
    if category_id:
        q = q.filter(KnowledgeEntry.category_id == category_id)
    total = q.count()
    items = q.order_by(KnowledgeEntry.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def get_entry(db: Session, entry_id: int) -> Optional[KnowledgeEntry]:
    return db.query(KnowledgeEntry).filter(KnowledgeEntry.id == entry_id, KnowledgeEntry.status == 1).first()


def create_entry(db: Session, hospital_id: str, title: str, content: str,
                 category_id: Optional[int] = None) -> KnowledgeEntry:
    entry = KnowledgeEntry(category_id=category_id, title=title, content=content, source_type="manual")
    db.add(entry)
    db.commit()
    db.refresh(entry)
    ai_rag.index_documents(hospital_id, [Document(text=content, metadata={
        "entry_id": entry.id, "title": title,
    })], category_id, "manual")
    return entry


def update_entry(db: Session, hospital_id: str, entry_id: int,
                 title: Optional[str] = None, content: Optional[str] = None,
                 category_id: Optional[int] = None) -> Optional[KnowledgeEntry]:
    entry = get_entry(db, entry_id)
    if not entry:
        return None
    if category_id is not None:
        entry.category_id = category_id
    if title is not None:
        entry.title = title
    if content is not None:
        entry.content = content
        db.commit()
        ai_rag.delete_vectors(hospital_id, entry.id)
        ai_rag.index_documents(hospital_id, [Document(text=content, metadata={
            "entry_id": entry.id, "title": entry.title,
        })], entry.category_id, entry.source_file or "manual")
    db.commit()
    db.refresh(entry)
    return entry


def delete_entry(db: Session, hospital_id: str, entry_id: int) -> bool:
    entry = get_entry(db, entry_id)
    if not entry:
        return False
    entry.status = 0
    db.commit()
    ai_rag.delete_vectors(hospital_id, entry_id)
    return True


def import_from_file(db: Session, hospital_id: str, file_path: str,
                     filename: str, category_id: Optional[int] = None) -> int:
    from app.ai.rag.readers import load_documents
    docs = load_documents(file_path, filename)
    if not docs:
        return 0

    for doc in docs:
        entry = KnowledgeEntry(
            category_id=category_id, title=filename,
            content=doc.text, source_type="import", source_file=filename,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        doc.metadata["entry_id"] = entry.id

    ai_rag.index_documents(hospital_id, docs, category_id, filename)
    return len(docs)


# ---- Search ----

def search(hospital_id: str, query: str, top_k: int = 5,
           category_ids: Optional[List[int]] = None) -> List[SearchResult]:
    return ai_rag.search(hospital_id, query, category_ids=category_ids, top_k=top_k)


def reindex_category(hospital_id: str, category_id: int):
    """全量重建某分类的向量（实际重建整个医院，因 Milvus collection 按医院隔离）"""
    db = next(get_hospital_db(hospital_id))
    try:
        entries = db.query(KnowledgeEntry).filter(
            KnowledgeEntry.status == 1,
            KnowledgeEntry.category_id == category_id,
        ).all()
        entry_dicts = [
            {"id": e.id, "title": e.title, "content": e.content,
             "category_id": e.category_id, "source_file": e.source_file}
            for e in entries
        ]
    finally:
        db.close()
    if entry_dicts:
        ai_rag.reindex_hospital(hospital_id, entry_dicts)
