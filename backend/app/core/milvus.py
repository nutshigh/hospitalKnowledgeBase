from typing import List, Dict, Optional
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility

from app.config import settings

COLLECTION_TEMPLATE = "hospital_{hospital_id}_knowledge"
VECTOR_DIM = 1024


class MilvusClient:
    def __init__(self):
        self._connected = False
        self._server_started = False

    def _start_server(self):
        if self._server_started:
            return
        from milvus_lite import server_manager
        server_manager.start(port=settings.MILVUS_PORT)
        self._server_started = True

    def _ensure_connection(self):
        if not self._connected:
            self._start_server()
            connections.connect(host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
            self._connected = True

    def get_collection_name(self, hospital_id: str) -> str:
        return COLLECTION_TEMPLATE.format(hospital_id=hospital_id)

    def create_collection(self, hospital_id: str):
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        if utility.has_collection(collection_name):
            return

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIM),
            FieldSchema(name="entry_id", dtype=DataType.INT64),
            FieldSchema(name="category_id", dtype=DataType.INT64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=200),
            FieldSchema(name="source_file", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="created_at", dtype=DataType.INT64),
        ]
        schema = CollectionSchema(fields, description=f"Knowledge vectors for hospital {hospital_id}")
        collection = Collection(name=collection_name, schema=schema)

        index_params = {"metric_type": "IP", "index_type": "IVF_FLAT", "params": {"nlist": 128}}
        collection.create_index(field_name="vector", index_params=index_params)
        collection.load()

    def drop_collection(self, hospital_id: str):
        self._ensure_connection()
        collection_name = self.get_collection_name(hospital_id)
        if utility.has_collection(collection_name):
            utility.drop_collection(collection_name)

    def insert(self, hospital_id: str, vectors: List[List[float]], metadata: List[dict]):
        self._ensure_connection()
        collection = Collection(name=self.get_collection_name(hospital_id))
        data = [
            [v for v in vectors],
            [m["entry_id"] for m in metadata],
            [m.get("category_id", 0) for m in metadata],
            [m.get("title", "") for m in metadata],
            [m.get("source_file", "") for m in metadata],
            [m.get("created_at", 0) for m in metadata],
        ]
        collection.insert(data)

    def search(
        self, hospital_id: str, query_vector: List[float],
        top_k: int = 5, filter_expr: Optional[str] = None,
    ) -> List[Dict]:
        self._ensure_connection()
        collection = Collection(name=self.get_collection_name(hospital_id))
        search_params = {"metric_type": "IP", "params": {"nprobe": 16}}
        results = collection.search(
            data=[query_vector], anns_field="vector", param=search_params,
            limit=top_k, expr=filter_expr,
            output_fields=["entry_id", "category_id", "title", "source_file"],
        )
        out = []
        for hits in results:
            for hit in hits:
                out.append({
                    "entry_id": hit.entity.get("entry_id"),
                    "category_id": hit.entity.get("category_id"),
                    "title": hit.entity.get("title"),
                    "source_file": hit.entity.get("source_file"),
                    "score": hit.score,
                })
        return out

    def delete_by_ids(self, hospital_id: str, ids: List[int]):
        self._ensure_connection()
        collection = Collection(name=self.get_collection_name(hospital_id))
        collection.delete(expr=f"entry_id in {ids}")

    def delete_by_criteria(self, hospital_id: str, expr: str):
        self._ensure_connection()
        collection = Collection(name=self.get_collection_name(hospital_id))
        collection.delete(expr=expr)

    def flush(self, hospital_id: str):
        self._ensure_connection()
        Collection(name=self.get_collection_name(hospital_id)).flush()


milvus_client = MilvusClient()
