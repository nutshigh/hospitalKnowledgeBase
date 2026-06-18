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


# SearchResult 已迁移到 app.ai.rag.types，此处重新导出以保持向后兼容
from app.ai.rag.types import SearchResult  # noqa: E402


class SearchResponse(BaseModel):
    results: List[SearchResult]
