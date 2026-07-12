import logging
from typing import List, Optional

import httpx
from pydantic import ConfigDict, Field, PrivateAttr
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever

from app.ai.config import get_embedding_model
from app.ai.rag.store import rag_store
from app.ai.rag.types import SearchResult
from app.config import settings

logger = logging.getLogger(__name__)

# 共享 httpx.Client，避免每次 RAGRetriever 构造时泄漏连接池
_shared_http_client: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.Client(timeout=30.0)
    return _shared_http_client


class HttpReranker(BaseNodePostprocessor):
    """调外部 reranker HTTP 服务的 postprocessor"""

    model_config = ConfigDict(extra="allow")

    top_n: int = Field(default=5, description="Number of nodes to return.")
    base_url: str = Field(default="", description="Reranker HTTP service base URL.")
    model: str = Field(default="", description="Reranker model name.")

    def __init__(self, top_n: int = 5, base_url: str = "", model: str = ""):
        base_url = base_url or settings.RERANKER_BASE_URL
        model = model or settings.RERANKER_MODEL
        super().__init__(top_n=top_n, base_url=base_url, model=model)

    @property
    def _client(self) -> httpx.Client:
        return _get_http_client()

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        if not nodes:
            return nodes
        query_str = query_bundle.query_str if query_bundle else ""
        documents = [n.node.text for n in nodes]
        try:
            resp = self._client.post(
                f"{self.base_url}/rerank",
                json={"query": query_str, "documents": documents, "top_n": self.top_n},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            ranked = []
            for r in results:
                idx = r["index"]
                if idx < len(nodes):
                    node = nodes[idx]
                    node.score = r["score"]
                    ranked.append(node)
            return ranked[:self.top_n]
        except Exception:
            return nodes[:self.top_n]


class RAGRetriever:
    """文档通道（向量+BM25融合→reranker）+ KG通道，分通道检索。"""

    def __init__(self, hospital_id: str):
        self.hospital_id = hospital_id
        index = rag_store.get_index(hospital_id)

        self._vector_retriever = index.as_retriever(
            similarity_top_k=settings.RAG_VECTOR_TOP_K,
        )

        nodes = rag_store.get_nodes(hospital_id)
        if nodes:
            self._bm25_retriever = BM25Retriever.from_nodes(
                nodes, similarity_top_k=settings.RAG_VECTOR_TOP_K
            )
        else:
            self._bm25_retriever = None

        retrievers = [self._vector_retriever]
        if self._bm25_retriever:
            retrievers.append(self._bm25_retriever)

        self._fusion = QueryFusionRetriever(
            retrievers,
            similarity_top_k=settings.RAG_VECTOR_TOP_K,
            mode="reciprocal_rerank",
        )
        self._reranker = HttpReranker(top_n=settings.RAG_FINAL_TOP_K)

    def _retrieve_documents(
        self,
        query: str,
        category_ids: Optional[List[int]] = None,
        top_k: Optional[int] = None,
    ) -> List[SearchResult]:
        """文档通道：向量+BM25融合 → reranker 重排。"""
        from llama_index.core.vector_stores import MetadataFilters, MetadataFilter

        filters = None
        if category_ids:
            filters = MetadataFilters(filters=[
                MetadataFilter(key="category_id", value=category_ids, operator="IN")
            ])

        try:
            nodes = self._fusion.retrieve(query, filters=filters)
        except Exception:
            try:
                nodes = self._vector_retriever.retrieve(query, filters=filters)
            except Exception:
                return []

        try:
            nodes = self._reranker.postprocess_nodes(nodes, query_str=query)
        except Exception:
            pass

        if top_k:
            nodes = nodes[:top_k]

        out = []
        for n in nodes:
            out.append(SearchResult(
                entry_id=n.metadata.get("entry_id", 0),
                title=n.metadata.get("title", ""),
                content=n.node.text,
                category_id=n.metadata.get("category_id"),
                score=float(n.score or 0),
                source="document",
            ))
        return out

    def retrieve(
        self,
        query: str,
        category_ids: Optional[List[int]] = None,
        top_k: Optional[int] = None,
    ) -> List[SearchResult]:
        """分通道检索：文档通道 + KG通道，结果分区组装。"""
        # 文档通道
        doc_results = self._retrieve_documents(query, category_ids, top_k)

        # KG 通道（独立检索，失败不影响文档结果）
        try:
            from app.ai.rag.kg_retriever import KGRetriever
            kg_retriever = KGRetriever(self.hospital_id)
            kg_results = kg_retriever.retrieve(query)
        except Exception as e:
            logger.warning("KG channel disabled (import/runtime error): %s", e)
            kg_results = []

        return doc_results + kg_results
