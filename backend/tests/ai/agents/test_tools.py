from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session


def test_make_tools_returns_six_tools():
    """make_tools 返回 6 个工具"""
    with patch("app.ai.agents.tools.ai_rag"):
        from app.ai.agents.tools import make_tools
        db = MagicMock(spec=Session)
        tools = make_tools("H001", db)
        assert len(tools) == 6
        names = {t.name for t in tools}
        assert names == {
            "search_knowledge", "get_report_indicators", "get_report_summary",
            "get_user_history_reports", "get_indicator_history", "get_triage_rules",
        }


def test_search_knowledge_tool_calls_rag():
    """search_knowledge 工具调 ai.rag.search"""
    with patch("app.ai.agents.tools.ai_rag") as mock_rag:
        from app.modules.knowledge.schemas import SearchResult
        mock_rag.search.return_value = [SearchResult(
            entry_id=1, title="t", content="c", category_id=2, score=0.9
        )]
        from app.ai.agents.tools import make_tools
        tools = make_tools("H001", MagicMock(spec=Session))
        search_tool = next(t for t in tools if t.name == "search_knowledge")
        result = search_tool.invoke({"query": "血糖"})
        assert isinstance(result, list)
        assert result[0]["entry_id"] == 1
        mock_rag.search.assert_called_once_with("H001", "血糖", category_ids=None, top_k=None)
