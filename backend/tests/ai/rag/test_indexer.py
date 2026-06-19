from unittest.mock import patch, MagicMock

from llama_index.core import Document


def test_index_documents_calls_pipeline_run():
    """index_documents 注入 metadata 后调 pipeline.run"""
    with patch("app.ai.rag.indexer.rag_store") as mock_store, \
         patch("app.ai.rag.indexer.get_embedding_model") as mock_embed, \
         patch("app.ai.rag.indexer.IngestionPipeline") as MockPipeline:
        mock_pipeline = MagicMock()
        MockPipeline.return_value = mock_pipeline
        mock_node = MagicMock()
        mock_node.node_id = "node-1"
        mock_pipeline.run.return_value = [mock_node]

        from app.ai.rag.indexer import RAGIndexer
        indexer = RAGIndexer("H001")
        docs = [Document(text="测试内容", metadata={})]
        ids = indexer.index_documents(docs, category_id=5, source_file="test.pdf")

        assert ids == ["node-1"]
        assert docs[0].metadata["category_id"] == 5
        assert docs[0].metadata["source_file"] == "test.pdf"
        assert docs[0].metadata["hospital_id"] == "H001"
        mock_store.refresh.assert_called_once_with("H001")


def test_reindex_all_drops_and_rebuilds():
    """reindex_all 调 rag_store.drop 并逐条 ingest"""
    with patch("app.ai.rag.indexer.rag_store") as mock_store, \
         patch("app.ai.rag.indexer.get_embedding_model"), \
         patch("app.ai.rag.indexer.IngestionPipeline") as MockPipeline:
        mock_pipeline = MagicMock()
        MockPipeline.return_value = mock_pipeline

        from app.ai.rag.indexer import RAGIndexer
        indexer = RAGIndexer("H001")
        entries = [
            {"id": 1, "title": "条目1", "content": "内容1", "category_id": 2, "source_file": "a.pdf"},
            {"id": 2, "title": "条目2", "content": "内容2", "category_id": 3, "source_file": "b.pdf"},
        ]
        indexer.reindex_all(entries)

        mock_store.drop.assert_called_once_with("H001")
        assert mock_pipeline.run.call_count == 2
