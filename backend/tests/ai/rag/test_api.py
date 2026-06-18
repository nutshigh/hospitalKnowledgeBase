from unittest.mock import patch, MagicMock

from llama_index.core import Document


def test_search_delegates_to_retriever():
    with patch("app.ai.rag.RAGRetriever") as MockRetriever:
        mock_inst = MagicMock()
        MockRetriever.return_value = mock_inst
        from app.modules.knowledge.schemas import SearchResult
        mock_inst.retrieve.return_value = [SearchResult(
            entry_id=1, title="t", content="c", category_id=2, score=0.9
        )]

        from app.ai.rag import search
        results = search("H001", "query")
        assert len(results) == 1
        assert results[0].entry_id == 1


def test_index_documents_delegates_to_indexer():
    with patch("app.ai.rag.RAGIndexer") as MockIndexer:
        mock_inst = MagicMock()
        MockIndexer.return_value = mock_inst
        mock_inst.index_documents.return_value = ["n1"]

        from app.ai.rag import index_documents
        ids = index_documents("H001", [Document(text="x")], 1, "f.pdf")
        assert ids == ["n1"]
