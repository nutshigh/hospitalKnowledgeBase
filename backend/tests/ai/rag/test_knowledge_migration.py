from unittest.mock import patch, MagicMock


def test_create_entry_calls_rag_index():
    """create_entry 后调 ai.rag.index_documents"""
    with patch("app.modules.knowledge.service.get_hospital_db") as mock_db_fn, \
         patch("app.modules.knowledge.service.ai_rag") as mock_rag:
        mock_db = MagicMock()
        mock_db_fn.return_value = iter([mock_db])
        mock_entry = MagicMock()
        mock_entry.id = 1
        mock_entry.content = "内容"
        mock_entry.title = "标题"
        mock_entry.category_id = None
        mock_entry.source_file = None
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        mock_db.refresh = MagicMock(side_effect=lambda x: setattr(x, "id", 1))

        from app.modules.knowledge import service
        service.create_entry(mock_db, "H001", "标题", "内容", None)
        mock_rag.index_documents.assert_called_once()


def test_search_delegates_to_ai_rag():
    """knowledge search 调 ai.rag.search"""
    with patch("app.modules.knowledge.service.ai_rag") as mock_rag:
        from app.modules.knowledge.schemas import SearchResult
        mock_rag.search.return_value = [SearchResult(
            entry_id=1, title="t", content="c", category_id=None, score=0.9
        )]
        from app.modules.knowledge import service
        results = service.search("H001", "query")
        assert len(results) == 1
        mock_rag.search.assert_called_once()
