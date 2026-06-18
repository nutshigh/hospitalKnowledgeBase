from typing import Optional

from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline, IngestionCache
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.storage.docstore import SimpleDocumentStore as SimpleKVStore

from app.ai.config import get_embedding_model
from app.ai.rag.store import rag_store
from app.config import settings


class RAGIndexer:
    """文档→chunk→embed→Milvus 的 LlamaIndex IngestionPipeline 封装"""

    _docstores: dict[str, SimpleKVStore] = {}
    _caches: dict[str, IngestionCache] = {}

    def __init__(self, hospital_id: str):
        self.hospital_id = hospital_id
        if hospital_id not in self._docstores:
            self._docstores[hospital_id] = SimpleKVStore()
            self._caches[hospital_id] = IngestionCache()
        self.pipeline = IngestionPipeline(
            transformations=[
                SentenceSplitter(
                    chunk_size=settings.RAG_CHUNK_SIZE,
                    chunk_overlap=settings.RAG_CHUNK_OVERLAP,
                ),
                get_embedding_model(),
            ],
            vector_store=rag_store.get(hospital_id),
            docstore=self._docstores[hospital_id],
            cache=self._caches[hospital_id],
        )

    def index_documents(
        self,
        docs: list[Document],
        category_id: Optional[int],
        source_file: str,
    ) -> list[str]:
        """批量入库，返回 node_ids"""
        for d in docs:
            d.metadata.update({
                "category_id": category_id or 0,
                "source_file": source_file,
                "hospital_id": self.hospital_id,
            })
        nodes = self.pipeline.run(documents=docs)
        rag_store.refresh(self.hospital_id)
        return [n.node_id for n in nodes]

    def delete_by_entry(self, entry_id: int):
        """按 entry_id 删 Milvus 向量"""
        rag_store.get(self.hospital_id).delete(
            filter={"entry_id": entry_id}
        )
        rag_store.refresh(self.hospital_id)

    def reindex_all(self, entries: list[dict]):
        """全量重建：drop collection → 逐条 ingest"""
        rag_store.drop(self.hospital_id)
        self._docstores[self.hospital_id] = SimpleKVStore()
        self._caches[self.hospital_id] = IngestionCache()
        self.pipeline.docstore = self._docstores[self.hospital_id]
        self.pipeline.cache = self._caches[self.hospital_id]
        for e in entries:
            docs = [Document(
                text=e["content"],
                metadata={
                    "entry_id": e["id"],
                    "category_id": e.get("category_id") or 0,
                    "title": e["title"],
                    "source_file": e.get("source_file") or "",
                },
            )]
            self.pipeline.run(documents=docs)
        rag_store.refresh(self.hospital_id)
