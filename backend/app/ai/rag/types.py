from typing import Optional
from pydantic import BaseModel


class SearchResult(BaseModel):
    """RAG 检索结果（ai 层拥有此类型，modules 层重新导出以保持兼容）"""
    entry_id: int
    title: str
    content: str
    category_id: Optional[int] = None
    score: float
