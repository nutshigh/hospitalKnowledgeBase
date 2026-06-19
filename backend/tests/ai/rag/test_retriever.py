from unittest.mock import patch, MagicMock


def test_retrieve_with_reranker_downgrade():
    """reranker 不可用时降级返回 fusion 结果"""
    with patch("app.ai.rag.retriever.rag_store") as mock_store, \
         patch("app.ai.rag.retriever.get_embedding_model"):
        mock_index = MagicMock()
        mock_store.get_index.return_value = mock_index
        mock_store.get_nodes.return_value = []

        from app.ai.rag.retriever import RAGRetriever
        retriever = RAGRetriever("H001")

        with patch.object(retriever._fusion, "retrieve") as mock_fusion, \
             patch.object(retriever._reranker, "postprocess_nodes", side_effect=Exception("conn refused")):
            mock_node = MagicMock()
            mock_node.node.text = "内容"
            mock_node.metadata = {"entry_id": 1, "title": "标题", "category_id": 2}
            mock_node.score = 0.9
            mock_fusion.return_value = [mock_node]

            results = retriever.retrieve("查询")
            assert len(results) == 1
            assert results[0].entry_id == 1
            assert results[0].content == "内容"


def test_retrieve_empty_results():
    """无结果时返回空列表"""
    with patch("app.ai.rag.retriever.rag_store") as mock_store, \
         patch("app.ai.rag.retriever.get_embedding_model"):
        mock_store.get_index.return_value = MagicMock()
        mock_store.get_nodes.return_value = []

        from app.ai.rag.retriever import RAGRetriever
        retriever = RAGRetriever("H001")

        with patch.object(retriever._fusion, "retrieve", return_value=[]):
            results = retriever.retrieve("查询")
            assert results == []
