from typing import List, Optional
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.milvus import milvus_client
from app.core.embedding import embedding_client
from app.modules.knowledge.models import KnowledgeCategory, KnowledgeEntry
from app.modules.knowledge.schemas import SearchResult


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
    _vectorize_entry(hospital_id, entry)
    db.commit()
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
        old_vector_id = entry.vector_id
        entry.content = content
        db.commit()
        if old_vector_id:
            milvus_client.delete_by_ids(hospital_id, [entry.id])
        _vectorize_entry(hospital_id, entry)
    db.commit()
    db.refresh(entry)
    return entry


def delete_entry(db: Session, hospital_id: str, entry_id: int) -> bool:
    entry = get_entry(db, entry_id)
    if not entry:
        return False
    entry.status = 0
    if entry.vector_id:
        milvus_client.delete_by_ids(hospital_id, [entry.id])
    db.commit()
    return True


def _vectorize_entry(hospital_id: str, entry: KnowledgeEntry):
    vector = embedding_client.embed_single(entry.content)
    meta = {
        "entry_id": entry.id,
        "category_id": entry.category_id or 0,
        "title": entry.title,
        "source_file": entry.source_file or "",
        "created_at": int(entry.created_at.timestamp()),
    }
    milvus_client.insert(hospital_id, [vector], [meta])
    milvus_client.flush(hospital_id)
    entry.vector_id = str(entry.id)


# ---- Import from file ----

def import_from_file(db: Session, hospital_id: str, file_path: str,
                     filename: str, category_id: Optional[int] = None) -> int:
    from app.core.doc_parser import parse_file
    chunks = parse_file(file_path, filename)
    if not chunks:
        return 0

    first_entry = KnowledgeEntry(
        category_id=category_id, title=filename, content=chunks[0].text,
        source_type="import", source_file=filename, chunk_index=0,
    )
    db.add(first_entry)
    db.commit()
    db.refresh(first_entry)
    _vectorize_entry(hospital_id, first_entry)

    for i, chunk in enumerate(chunks[1:], start=1):
        sub = KnowledgeEntry(
            category_id=category_id, title=f"{filename} (Part {i + 1})",
            content=chunk.text, source_type="import", source_file=filename,
            chunk_index=i, parent_entry_id=first_entry.id,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        _vectorize_entry(hospital_id, sub)

    return len(chunks)


# ---- Search ----

def search(hospital_id: str, query: str, top_k: int = 5,
           category_ids: Optional[List[int]] = None) -> List[SearchResult]:
    query_vector = embedding_client.embed_single(query)
    filter_expr = None
    if category_ids:
        ids_str = ", ".join(str(c) for c in category_ids)
        filter_expr = f"category_id in [{ids_str}]"

    results = milvus_client.search(hospital_id, query_vector, top_k=top_k, filter_expr=filter_expr)

    entry_ids = [r["entry_id"] for r in results]
    content_map = {}
    if entry_ids:
        db = next(get_hospital_db(hospital_id))
        try:
            entries = db.query(KnowledgeEntry).filter(KnowledgeEntry.id.in_(entry_ids)).all()
            content_map = {e.id: e.content for e in entries}
        finally:
            db.close()

    out = []
    for r in results:
        out.append(SearchResult(
            entry_id=r["entry_id"],
            title=r["title"],
            content=content_map.get(r["entry_id"], ""),
            category_id=r.get("category_id"),
            score=r["score"],
        ))
    return out


def reindex_category(hospital_id: str, category_id: int):
    milvus_client.delete_by_criteria(hospital_id, f"category_id == {category_id}")
    milvus_client.flush(hospital_id)
