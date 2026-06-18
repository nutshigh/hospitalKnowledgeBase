from unittest.mock import patch, MagicMock


def test_rag_store_caches_per_hospital():
    """同一 hospital_id 的 MilvusVectorStore 只创建一次"""
    with patch("app.ai.config.ensure_milvus_started"), \
         patch("llama_index.vector_stores.milvus.MilvusVectorStore") as MockVS:
        from app.ai.rag.store import RAGStore
        store = RAGStore()
        MockVS.side_effect = lambda *a, **kw: MagicMock()
        s1 = store.get("H001")
        s2 = store.get("H001")
        assert s1 is s2
        MockVS.assert_called_once()
        s3 = store.get("H002")
        assert s3 is not s1
        assert MockVS.call_count == 2


def test_rag_store_refresh_clears_cache():
    """refresh 后下次 get 重建"""
    with patch("app.ai.config.ensure_milvus_started"), \
         patch("llama_index.vector_stores.milvus.MilvusVectorStore") as MockVS:
        from app.ai.rag.store import RAGStore
        store = RAGStore()
        MockVS.return_value = MagicMock()
        store.get("H001")
        assert "H001" in store._stores
        store.refresh("H001")
        assert "H001" not in store._indices
