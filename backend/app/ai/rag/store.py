from typing import Optional

from app.ai.config import ensure_milvus_started, get_embedding_model, VECTOR_DIM
from app.config import settings


class RAGStore:
    """按医院隔离的 LlamaIndex MilvusVectorStore 单例工厂"""

    def __init__(self):
        self._stores: dict[str, "MilvusVectorStore"] = {}
        self._indices: dict[str, "VectorStoreIndex"] = {}
        self._nodes_cache: dict[str, list] = {}

    def get(self, hospital_id: str):
        """获取或创建某医院的 MilvusVectorStore"""
        ensure_milvus_started()
        if hospital_id not in self._stores:
            from llama_index.vector_stores.milvus import MilvusVectorStore

            self._stores[hospital_id] = MilvusVectorStore(
                uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
                collection_name=f"hospital_{hospital_id}_knowledge",
                dim=VECTOR_DIM,
                overwrite=False,
                metric_type="IP",
                index_config={
                    "index_type": "IVF_FLAT",
                    "params": {"nlist": 128},
                },
            )
        return self._stores[hospital_id]

    def get_index(self, hospital_id: str):
        """构造 VectorStoreIndex 并缓存，供 VectorIndexRetriever 使用"""
        if hospital_id not in self._indices:
            from llama_index.core import VectorStoreIndex

            self._indices[hospital_id] = VectorStoreIndex(
                vector_store=self.get(hospital_id),
                embed_model=get_embedding_model(),
            )
        return self._indices[hospital_id]

    def get_nodes(self, hospital_id: str) -> list:
        """拉取所有节点供 BM25Retriever 构建"""
        if hospital_id not in self._nodes_cache:
            index = self.get_index(hospital_id)
            self._nodes_cache[hospital_id] = list(index.docstore.docs.values())
        return self._nodes_cache[hospital_id]

    def refresh(self, hospital_id: str):
        """知识库更新后清缓存，下次 get_* 重建"""
        self._indices.pop(hospital_id, None)
        self._nodes_cache.pop(hospital_id, None)

    def drop(self, hospital_id: str):
        """reindex 用：drop collection 并清缓存"""
        from pymilvus import utility

        ensure_milvus_started()
        collection_name = f"hospital_{hospital_id}_knowledge"
        if utility.has_collection(collection_name):
            utility.drop_collection(collection_name)
        self._stores.pop(hospital_id, None)
        self.refresh(hospital_id)


rag_store = RAGStore()
