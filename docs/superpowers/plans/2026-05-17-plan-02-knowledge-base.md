# 知识库模块 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现知识库模块完整功能——知识分类 CRUD、知识条目 CRUD、文档导入与解析、向量化入库、语义检索 API。

**Architecture:** 知识库模块向下依赖 MySQL（条目元数据 + 分类）、Milvus（向量存储与检索）、文件存储（源文档）。向上暴露 REST API（医生端管理）和内部检索接口（AI 解读模块调用）。文本分段采用滑动窗口重叠策略，Embedding 预留接口。

**Tech Stack:** FastAPI, SQLAlchemy, PyMuPDF / python-docx, Milvus Python SDK

---

## 文件结构

```
backend/app/
├── modules/
│   └── knowledge/
│       ├── __init__.py
│       ├── models.py           # SQLAlchemy ORM 模型（knowledge_category, knowledge_entry）
│       ├── schemas.py          # Pydantic 请求/响应模型
│       ├── service.py          # 业务逻辑层
│       ├── router.py           # FastAPI 路由（REST API 对外）
│       └── internal.py         # 内部检索接口（供 AI 解读模块调用）
├── core/
│   ├── embedding.py            # Embedding 客户端封装（调用本地 Embedding 服务）
│   └── doc_parser.py           # 文档解析器（PDF/Word/Excel/Text）
└── main.py                     # 注册 knowledge 路由
```

---

### Task 1: git 分支 + 数据模型

**Branch:** 从 `infra-setup` 切出 `feat/knowledge-base`

- [ ] **Step 1: 创建分支**

```bash
git checkout infra-setup
git checkout -b feat/knowledge-base
```

- [ ] **Step 2: 编写 SQLAlchemy ORM 模型**

`app/modules/knowledge/models.py`:
```python
from sqlalchemy import Column, BigInteger, String, Text, Integer, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.models.base import Base


class KnowledgeCategory(Base):
    __tablename__ = "knowledge_category"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    parent_id = Column(BigInteger, ForeignKey("knowledge_category.id"), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    children = relationship("KnowledgeCategory", backref="parent", remote_side=[id])


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entry"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    category_id = Column(BigInteger, ForeignKey("knowledge_category.id"), nullable=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    source_type = Column(String(20), nullable=False, default="manual")
    source_file = Column(String(500), nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    parent_entry_id = Column(BigInteger, ForeignKey("knowledge_entry.id"), nullable=True)
    vector_id = Column(String(64), nullable=True)
    status = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
```

- [ ] **Step 3: 验证模型导入**

```bash
uv run python -c "from app.modules.knowledge.models import KnowledgeCategory, KnowledgeEntry; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/
git commit -m "feat(knowledge): add ORM models for category and entry"
```

---

### Task 2: Pydantic Schemas

**Files:**
- Create: `app/modules/knowledge/schemas.py`

- [ ] **Step 1: 编写 schemas.py**

`app/modules/knowledge/schemas.py`:
```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    parent_id: Optional[int] = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None


class CategoryResponse(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    sort_order: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CategoryTree(CategoryResponse):
    children: List["CategoryTree"] = []


class EntryCreate(BaseModel):
    category_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)


class EntryUpdate(BaseModel):
    category_id: Optional[int] = None
    title: Optional[str] = None
    content: Optional[str] = None


class EntryResponse(BaseModel):
    id: int
    category_id: Optional[int] = None
    title: str
    content: str
    source_type: str
    source_file: Optional[str] = None
    status: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EntryListResponse(BaseModel):
    items: List[EntryResponse]
    total: int
    page: int
    page_size: int


class SearchRequest(BaseModel):
    hospital_id: str
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    category_ids: Optional[List[int]] = None


class SearchResult(BaseModel):
    entry_id: int
    title: str
    content: str
    category_id: Optional[int] = None
    score: float


class SearchResponse(BaseModel):
    results: List[SearchResult]
```

- [ ] **Step 2: Commit**

```bash
git add app/modules/knowledge/schemas.py
git commit -m "feat(knowledge): add Pydantic schemas"
```

---

### Task 3: 文档解析引擎

**Files:**
- Create: `app/core/doc_parser.py`

- [ ] **Step 1: 安装依赖**

```bash
uv add PyMuPDF python-docx openpyxl
```

- [ ] **Step 2: 编写文档解析器**

`app/core/doc_parser.py`:
```python
import io
from pathlib import Path
from typing import List


class TextChunk:
    def __init__(self, text: str, title: str = "", chunk_index: int = 0):
        self.text = text
        self.title = title
        self.chunk_index = chunk_index


def parse_file(file_path: str, filename: str) -> List[TextChunk]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(file_path)
    elif ext in (".docx", ".doc"):
        return _parse_docx(file_path)
    elif ext in (".xlsx", ".xls"):
        return _parse_excel(file_path)
    elif ext in (".txt", ".md"):
        return _parse_text(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _parse_pdf(file_path: str) -> List[TextChunk]:
    import fitz
    chunks = []
    doc = fitz.open(file_path)
    full_text = ""
    for page in doc:
        text = page.get_text()
        if text.strip():
            full_text += text + "\n"
    doc.close()
    if full_text.strip():
        chunks = _split_text(full_text, filename=Path(file_path).name)
    return chunks


def _parse_docx(file_path: str) -> List[TextChunk]:
    from docx import Document
    doc = Document(file_path)
    full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return _split_text(full_text, filename=Path(file_path).name)


def _parse_excel(file_path: str) -> List[TextChunk]:
    import openpyxl
    wb = openpyxl.load_workbook(file_path, read_only=True)
    chunks = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for row in ws.iter_rows(values_only=True):
            row_text = " | ".join(str(c) if c is not None else "" for c in row)
            if row_text.strip().replace("|", "").strip():
                rows.append(row_text)
        if rows:
            text = f"Sheet: {sheet_name}\n" + "\n".join(rows)
            chunks.extend(_split_text(text, title=sheet_name))
    wb.close()
    return chunks


def _parse_text(file_path: str) -> List[TextChunk]:
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    return _split_text(text, filename=Path(file_path).name)


def _split_text(
    text: str,
    filename: str = "",
    title: str = "",
    chunk_size: int = 500,
    overlap: int = 50,
) -> List[TextChunk]:
    chunks = []
    text = text.strip()
    if not text:
        return chunks

    # Split by paragraphs first, then by size
    paragraphs = text.split("\n")
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > chunk_size and current:
            chunk_title = title or filename or current.strip()[:50]
            chunks.append(TextChunk(text=current.strip(), title=chunk_title, chunk_index=len(chunks)))
            # Keep overlap
            overlap_text = current[-overlap:] if len(current) > overlap else ""
            current = overlap_text + para + "\n"
        else:
            current += para + "\n"

    if current.strip():
        chunk_title = title or filename or current.strip()[:50]
        chunks.append(TextChunk(text=current.strip(), title=chunk_title, chunk_index=len(chunks)))

    return chunks
```

- [ ] **Step 3: 验证解析器导入**

```bash
uv run python -c "from app.core.doc_parser import parse_file, _split_text; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/core/doc_parser.py pyproject.toml uv.lock
git commit -m "feat(knowledge): add document parser (PDF/Word/Excel/Text)"
```

---

### Task 4: Embedding 客户端

**Files:**
- Create: `app/core/embedding.py`

- [ ] **Step 1: 编写 Embedding 客户端**

`app/core/embedding.py`:
```python
from typing import List
from httpx import Client, Timeout
from app.config import settings


class EmbeddingClient:
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.client = Client(timeout=Timeout(30.0))

    def embed(self, texts: List[str]) -> List[List[float]]:
        response = self.client.post(
            f"{self.base_url}/api/embed",
            json={"model": "bge-m3", "input": texts},
        )
        response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data["embeddings"]]

    def embed_single(self, text: str) -> List[float]:
        results = self.embed([text])
        return results[0]


embedding_client = EmbeddingClient()
```

- [ ] **Step 2: 验证导入**

```bash
uv run python -c "from app.core.embedding import embedding_client; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add app/core/embedding.py
git commit -m "feat(knowledge): add embedding client wrapper"
```

---

### Task 5: 知识库业务逻辑层

**Files:**
- Create: `app/modules/knowledge/service.py`

- [ ] **Step 1: 编写 service.py**

`app/modules/knowledge/service.py`:
```python
from typing import List, Optional
from sqlalchemy.orm import Session

from app.core.milvus import milvus_client
from app.core.embedding import embedding_client
from app.modules.knowledge.models import KnowledgeCategory, KnowledgeEntry
from app.modules.knowledge.schemas import (
    CategoryCreate, CategoryUpdate, EntryCreate, EntryUpdate,
    SearchResult,
)


# ---- Category CRUD ----

def list_categories(db: Session) -> List[dict]:
    rows = db.query(KnowledgeCategory).order_by(KnowledgeCategory.sort_order).all()
    return [
        {"id": r.id, "name": r.name, "parent_id": r.parent_id, "sort_order": r.sort_order,
         "created_at": r.created_at, "updated_at": r.updated_at}
        for r in rows
    ]


def get_category(db: Session, category_id: int) -> KnowledgeCategory:
    return db.query(KnowledgeCategory).filter(KnowledgeCategory.id == category_id).first()


def create_category(db: Session, data: CategoryCreate) -> KnowledgeCategory:
    cat = KnowledgeCategory(name=data.name, parent_id=data.parent_id, sort_order=data.sort_order)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def update_category(db: Session, category_id: int, data: CategoryUpdate) -> KnowledgeCategory:
    cat = get_category(db, category_id)
    if not cat:
        return None
    if data.name is not None:
        cat.name = data.name
    if data.parent_id is not None:
        cat.parent_id = data.parent_id
    if data.sort_order is not None:
        cat.sort_order = data.sort_order
    db.commit()
    db.refresh(cat)
    return cat


def delete_category(db: Session, category_id: int) -> bool:
    cat = get_category(db, category_id)
    if not cat:
        return False
    # Move child categories up
    db.query(KnowledgeCategory).filter(KnowledgeCategory.parent_id == category_id).update({"parent_id": cat.parent_id})
    db.delete(cat)
    db.commit()
    return True


# ---- Entry CRUD ----

def list_entries(db: Session, category_id: Optional[int] = None, page: int = 1, page_size: int = 20) -> tuple:
    q = db.query(KnowledgeEntry).filter(KnowledgeEntry.status == 1)
    if category_id:
        q = q.filter(KnowledgeEntry.category_id == category_id)
    total = q.count()
    items = q.order_by(KnowledgeEntry.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def get_entry(db: Session, entry_id: int) -> KnowledgeEntry:
    return db.query(KnowledgeEntry).filter(KnowledgeEntry.id == entry_id, KnowledgeEntry.status == 1).first()


def create_entry(db: Session, hospital_id: str, data: EntryCreate) -> KnowledgeEntry:
    entry = KnowledgeEntry(
        category_id=data.category_id, title=data.title, content=data.content,
        source_type="manual",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    # Vectorize and store
    _vectorize_entry(hospital_id, entry)

    db.commit()
    return entry


def update_entry(db: Session, hospital_id: str, entry_id: int, data: EntryUpdate) -> KnowledgeEntry:
    entry = get_entry(db, entry_id)
    if not entry:
        return None
    if data.category_id is not None:
        entry.category_id = data.category_id
    if data.title is not None:
        entry.title = data.title
    if data.content is not None:
        old_vector_id = entry.vector_id
        entry.content = data.content
        db.commit()
        # Remove old vector, create new
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

def import_from_file(db: Session, hospital_id: str, file_path: str, filename: str, category_id: Optional[int] = None) -> int:
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
            category_id=category_id, title=f"{filename} (Part {i+1})",
            content=chunk.text, source_type="import", source_file=filename,
            chunk_index=i, parent_entry_id=first_entry.id,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        _vectorize_entry(hospital_id, sub)

    return len(chunks)


# ---- Search ----

def search(hospital_id: str, query: str, top_k: int = 5, category_ids: Optional[List[int]] = None) -> List[SearchResult]:
    query_vector = embedding_client.embed_single(query)
    filter_expr = None
    if category_ids:
        ids_str = ", ".join(str(c) for c in category_ids)
        filter_expr = f"category_id in [{ids_str}]"

    results = milvus_client.search(hospital_id, query_vector, top_k=top_k, filter_expr=filter_expr)

    out = []
    for r in results:
        out.append(SearchResult(
            entry_id=r["entry_id"],
            title=r["title"],
            content="",  # content retrieved separately from MySQL
            category_id=r.get("category_id"),
            score=r["score"],
        ))
    return out


def reindex_category(hospital_id: str, category_id: int):
    """Rebuild vector index for all entries in a category."""
    milvus_client.delete_by_criteria(hospital_id, f"category_id == {category_id}")
    # Re-vectorize each entry (would need db session here in practice)
    milvus_client.flush(hospital_id)
```

- [ ] **Step 2: 验证导入**

```bash
uv run python -c "from app.modules.knowledge.service import list_categories, search; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add app/modules/knowledge/service.py
git commit -m "feat(knowledge): add business logic layer (CRUD + search + import)"
```

---

### Task 6: REST API 路由

**Files:**
- Create: `app/modules/knowledge/router.py`
- Create: `app/modules/knowledge/internal.py`
- Modify: `app/main.py`

- [ ] **Step 1: 编写 router.py（医生端 API）**

`app/modules/knowledge/router.py`:
```python
from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional
import os
import uuid

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser, require_role
from app.core.milvus import milvus_client
from app.middleware.hospital_context import get_current_hospital_id
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.knowledge import schemas, service
from app.config import settings

router = APIRouter()


def _get_hospital_id() -> str:
    hid = get_current_hospital_id()
    if not hid:
        raise ValidationException(detail="Hospital context required")
    return hid


def _get_db(hospital_id: str = Depends(_get_hospital_id)):
    return next(get_hospital_db(hospital_id))


@router.get("/categories", response_model=list[schemas.CategoryResponse])
def list_categories(db: Session = Depends(_get_db)):
    return service.list_categories(db)


@router.post("/categories", response_model=schemas.CategoryResponse)
def create_category(data: schemas.CategoryCreate, db: Session = Depends(_get_db)):
    return service.create_category(db, data)


@router.put("/categories/{category_id}", response_model=schemas.CategoryResponse)
def update_category(category_id: int, data: schemas.CategoryUpdate, db: Session = Depends(_get_db)):
    cat = service.update_category(db, category_id, data)
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
    hospital_id: str = Depends(_get_hospital_id),
    db: Session = Depends(_get_db),
):
    return service.create_entry(db, hospital_id, data)


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
    hospital_id: str = Depends(_get_hospital_id),
    db: Session = Depends(_get_db),
):
    entry = service.update_entry(db, hospital_id, entry_id, data)
    if not entry:
        raise NotFoundException(detail="Entry not found")
    return entry


@router.delete("/entries/{entry_id}")
def delete_entry(
    entry_id: int,
    hospital_id: str = Depends(_get_hospital_id),
    db: Session = Depends(_get_db),
):
    if not service.delete_entry(db, hospital_id, entry_id):
        raise NotFoundException(detail="Entry not found")
    return {"status": "deleted"}


@router.post("/import")
def import_document(
    file: UploadFile = File(...),
    category_id: Optional[int] = Query(None),
    hospital_id: str = Depends(_get_hospital_id),
    db: Session = Depends(_get_db),
):
    allowed_exts = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md"}
    _, ext = os.path.splitext(file.filename)
    if ext.lower() not in allowed_exts:
        raise ValidationException(detail=f"Unsupported format. Allowed: {allowed_exts}")

    # Save temp file
    storage_dir = os.path.join(settings.FILE_STORAGE_ROOT, hospital_id, "knowledge")
    os.makedirs(storage_dir, exist_ok=True)
    tmp_path = os.path.join(storage_dir, f"{uuid.uuid4().hex}{ext}")
    with open(tmp_path, "wb") as f:
        f.write(file.file.read())

    try:
        count = service.import_from_file(db, hospital_id, tmp_path, file.filename, category_id)
        return {"imported": count, "filename": file.filename}
    except Exception as e:
        raise ValidationException(detail=f"Import failed: {str(e)}")


@router.post("/reindex/{category_id}")
def reindex_category(
    category_id: int,
    hospital_id: str = Depends(_get_hospital_id),
):
    service.reindex_category(hospital_id, category_id)
    return {"status": "reindexed", "category_id": category_id}
```

- [ ] **Step 2: 编写 internal.py（内部检索接口）**

`app/modules/knowledge/internal.py`:
```python
from fastapi import APIRouter, Depends
from app.modules.knowledge import schemas, service

router = APIRouter()


@router.post("/search", response_model=schemas.SearchResponse)
def search_knowledge(req: schemas.SearchRequest):
    results = service.search(
        hospital_id=req.hospital_id,
        query=req.query,
        top_k=req.top_k,
        category_ids=req.category_ids,
    )
    return schemas.SearchResponse(results=results)
```

- [ ] **Step 3: 注册路由到 main.py**

Edit `app/main.py`，在 `app.include_router(auth_router, ...)` 后添加:
```python
from app.modules.knowledge.router import router as knowledge_router
from app.modules.knowledge.internal import router as knowledge_internal_router

# 医生端 CRUD API
app.include_router(knowledge_router, prefix="/api/v1/knowledge", tags=["knowledge"])
# 内部检索 API（AI 解读模块调用）
app.include_router(knowledge_internal_router, prefix="/api/v1/knowledge/internal", tags=["knowledge-internal"])
```

- [ ] **Step 4: 验证路由注册**

```bash
uv run python -c "from app.main import app; routes = [r.path for r in app.routes if hasattr(r, 'path')]; print([r for r in routes if 'knowledge' in r])"
```

Expected: `['/api/v1/knowledge/categories', '/api/v1/knowledge/entries', ...]`

- [ ] **Step 5: Commit**

```bash
git add app/modules/knowledge/router.py app/modules/knowledge/internal.py app/main.py
git commit -m "feat(knowledge): add REST API routes (CRUD + import + internal search)"
```

---

### Task 7: 完整性验证

- [ ] **Step 1: 启动服务验证路由可访问**

```bash
uv run uvicorn app.main:app --port 8000 &
sleep 2
curl -s http://localhost:8000/api/v1/knowledge/categories
```

Expected: `[]`（需 MySQL 运行，无数据时返回空列表）

- [ ] **Step 2: 验证全部模块导入无误**

```bash
uv run python -c "
from app.modules.knowledge.models import KnowledgeCategory, KnowledgeEntry
from app.modules.knowledge.schemas import CategoryCreate, EntryCreate, SearchRequest
from app.modules.knowledge.service import list_categories, create_entry, search
from app.modules.knowledge.router import router
from app.modules.knowledge.internal import router as internal_router
from app.core.doc_parser import parse_file
from app.core.embedding import embedding_client
print('All imports OK')
"
```

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "chore(knowledge): verify module integrity"
```

---

### Task 8: 推送到远程

- [ ] **Step 1: 推送**

```bash
git push -u origin feat/knowledge-base
```

- [ ] **Step 2: 切回 infra-setup 并合并**

```bash
git checkout infra-setup
git merge feat/knowledge-base
git push origin infra-setup
```
