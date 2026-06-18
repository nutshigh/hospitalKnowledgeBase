from typing import List, Optional

from llama_index.core import Document

from app.ai.rag.indexer import RAGIndexer
from app.ai.rag.retriever import RAGRetriever
from app.modules.knowledge.schemas import SearchResult


def index_documents(
    hospital_id: str,
    docs: List[Document],
    category_id: Optional[int],
    source_file: str,
) -> List[str]:
    """入库文档到指定医院的向量库"""
    indexer = RAGIndexer(hospital_id)
    return indexer.index_documents(docs, category_id, source_file)


def delete_vectors(hospital_id: str, entry_id: int) -> None:
    """删除指定条目的向量"""
    indexer = RAGIndexer(hospital_id)
    indexer.delete_by_entry(entry_id)


def search(
    hospital_id: str,
    query: str,
    category_ids: Optional[List[int]] = None,
    top_k: Optional[int] = None,
) -> List[SearchResult]:
    """混合检索 + rerank"""
    retriever = RAGRetriever(hospital_id)
    return retriever.retrieve(query, category_ids=category_ids, top_k=top_k)


def reindex_hospital(hospital_id: str, entries: List[dict]) -> None:
    """全量重建某医院知识库向量"""
    indexer = RAGIndexer(hospital_id)
    indexer.reindex_all(entries)
