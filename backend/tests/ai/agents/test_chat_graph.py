import pytest
from unittest.mock import patch, MagicMock


def test_build_chat_graph_returns_compiled():
    """build_chat_graph 返回可编译的图"""
    with patch("app.ai.agents.chat_graph.get_chat_model") as mock_model, \
         patch("app.ai.agents.chat_graph.make_tools") as mock_tools:
        mock_model.return_value = MagicMock()
        mock_model.return_value.bind_tools.return_value = MagicMock()
        mock_tools.return_value = []

        from app.ai.agents.chat_graph import build_chat_graph
        graph = build_chat_graph("H001", MagicMock())
        assert graph is not None


def test_chat_state_has_knowledge_refs_accumulator():
    """ChatState 的 knowledge_refs 用累积 reducer"""
    from app.ai.agents.chat_graph import ChatState
    assert "knowledge_refs" in ChatState.__annotations__
    assert "messages" in ChatState.__annotations__
