"""知识图谱检索器，独立于文档检索通道。

将 KGClient 的结果包装为 SearchResult[source="knowledge_graph"]，
与文档检索结果分区组装后返回给 LLM。
"""
import logging

from app.ai.rag.kg_client import kg_client
from app.ai.rag.types import SearchResult
from app.config import settings

logger = logging.getLogger(__name__)


class KGRetriever:
    """知识图谱检索器。"""

    def __init__(self, hospital_id: str):
        self._hospital_id = hospital_id
        self._top_k = settings.KG_TOP_K

    def retrieve(self, query: str) -> list[SearchResult]:
        """KG 检索，返回 source='knowledge_graph' 的 SearchResult 列表。"""
        if not settings.KG_ENABLED:
            return []
        if not kg_client.is_available():
            return []
        try:
            kg_results = kg_client.search_entities(query, self._top_k)
            return [
                SearchResult(
                    entry_id=None,
                    title=r.entity,
                    content=r.text,
                    category_id=None,
                    score=r.score,
                    source="knowledge_graph",
                )
                for r in kg_results
            ]
        except Exception as e:
            logger.warning("KG retrieve failed: %s", e)
            return []
