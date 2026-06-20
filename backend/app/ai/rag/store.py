from typing import Optional

from app.ai.config import ensure_milvus_started, get_milvus_uri, get_embedding_model, VECTOR_DIM
from app.config import settings


def _patch_milvus_vector_store_init():
    """为 llama-index-vector-stores-milvus 0.4.0 打补丁。

    0.4.0 在 __init__ 里用 ORM ``Collection(using=self.client._using)``，但
    pymilvus 2.6 的 ``MilvusClient`` 不再向 ORM ``connections`` 注册 alias，
    导致 ``ConnectionNotExistException``。这里包装 ``MilvusClient.__init__``，
    在 client 创建后立刻用它的 alias 注册一条 ORM 连接，使后续
    ``Collection(using=...)`` 可用。只打一次补丁，幂等。
    """
    from pymilvus.milvus_client.milvus_client import MilvusClient
    if getattr(MilvusClient, "_orm_alias_patched", False):
        return
    from pymilvus import connections

    _orig_init = MilvusClient.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        alias = self._using
        try:
            connections._fetch_handler(alias)
        except Exception:
            connections.connect(alias=alias, uri=self._config.uri)

    MilvusClient.__init__ = _patched_init
    MilvusClient._orm_alias_patched = True


_patch_milvus_vector_store_init()


class RAGStore:
    """按医院隔离的 LlamaIndex MilvusVectorStore 单例工厂"""

    def __init__(self):
        self._stores: dict[str, "MilvusVectorStore"] = {}
        self._indices: dict[str, "VectorStoreIndex"] = {}
        self._nodes_cache: dict[str, list] = {}
        self._docstores: dict[str, "SimpleDocumentStore"] = {}

    def get(self, hospital_id: str):
        """获取或创建某医院的 MilvusVectorStore"""
        ensure_milvus_started()
        if hospital_id not in self._stores:
            from llama_index.vector_stores.milvus import MilvusVectorStore

            self._stores[hospital_id] = MilvusVectorStore(
                uri=get_milvus_uri(),
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
                nodes=[],
                vector_store=self.get(hospital_id),
                embed_model=get_embedding_model(),
            )
        return self._indices[hospital_id]

    def get_docstore(self, hospital_id: str):
        """获取或创建某医院的 docstore（与 IngestionPipeline 共享，供 BM25 读节点）"""
        if hospital_id not in self._docstores:
            from llama_index.core.storage.docstore import SimpleDocumentStore
            self._docstores[hospital_id] = SimpleDocumentStore()
        return self._docstores[hospital_id]

    def get_nodes(self, hospital_id: str) -> list:
        """拉取所有节点供 BM25Retriever 构建"""
        if hospital_id not in self._nodes_cache:
            docstore = self._docstores.get(hospital_id)
            if docstore:
                self._nodes_cache[hospital_id] = list(docstore.docs.values())
            else:
                self._nodes_cache[hospital_id] = []
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
        self._docstores.pop(hospital_id, None)
        self.refresh(hospital_id)


rag_store = RAGStore()
