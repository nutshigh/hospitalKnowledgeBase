import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock

from langchain.messages import ToolMessage, SystemMessage
from langgraph.types import Command


def test_chat_agent_state_has_knowledge_refs():
    """ChatAgentState 有 knowledge_refs 字段"""
    from app.ai.agents.chat_graph import ChatAgentState
    assert "knowledge_refs" in ChatAgentState.__annotations__
    assert "messages" in ChatAgentState.__annotations__


def test_build_chat_agent_returns_compiled():
    """build_chat_agent 返回非 None（create_agent 产物）"""
    with patch("app.ai.agents.chat_graph.get_chat_model") as mock_model:
        mock_model.return_value = MagicMock()
        from app.ai.agents.chat_graph import build_chat_agent
        agent = build_chat_agent(report_id=None)
        assert agent is not None


def test_build_chat_graph_removed():
    """旧 build_chat_graph 函数已删除"""
    import app.ai.agents.chat_graph as mod
    assert not hasattr(mod, "build_chat_graph")


def test_chat_state_removed():
    """旧 ChatState 已删除"""
    import app.ai.agents.chat_graph as mod
    assert not hasattr(mod, "ChatState")


def test_knowledge_refs_middleware_extracts_refs():
    """KnowledgeRefsMiddleware 从 search_knowledge 的 ToolMessage 提取 refs"""
    from app.ai.agents.chat_graph import KnowledgeRefsMiddleware

    mw = KnowledgeRefsMiddleware()
    tool_msg = ToolMessage(
        content=json.dumps([{"entry_id": 1, "title": "血糖知识", "content": "...", "score": 0.9, "source": "document"}]),
        tool_call_id="call_1",
    )
    request = MagicMock()
    request.tool_call = {"name": "search_knowledge", "id": "call_1", "args": {}}
    handler = MagicMock(return_value=tool_msg)

    result = mw.wrap_tool_call(request, handler)
    assert isinstance(result, Command)
    refs = result.update["knowledge_refs"]
    assert len(refs) == 1
    assert refs[0]["entry_id"] == 1
    assert refs[0]["title"] == "血糖知识"
    assert refs[0]["source"] == "document"


def test_knowledge_refs_middleware_ignores_other_tools():
    """非 search_knowledge 的工具调用，中间件直接返回原结果"""
    from app.ai.agents.chat_graph import KnowledgeRefsMiddleware

    mw = KnowledgeRefsMiddleware()
    tool_msg = ToolMessage(content="[]", tool_call_id="call_2")
    request = MagicMock()
    request.tool_call = {"name": "get_triage_rules", "id": "call_2", "args": {}}
    handler = MagicMock(return_value=tool_msg)

    result = mw.wrap_tool_call(request, handler)
    assert result is tool_msg


def test_report_context_middleware_appends_report_id():
    """ReportContextMiddleware 把 report_id 追加到 system_message"""
    from app.ai.agents.chat_graph import ReportContextMiddleware
    from langchain.agents.middleware import ModelRequest

    mw = ReportContextMiddleware(report_id=42)
    request = ModelRequest(
        model=MagicMock(),
        messages=[],
        system_message=SystemMessage(content="你是助手"),
    )
    handler = MagicMock(return_value="response")

    mw.wrap_model_call(request, handler)
    handler.assert_called_once()
    passed = handler.call_args[0][0]
    blocks = list(passed.system_message.content_blocks)
    assert any("42" in str(b.get("text", "")) for b in blocks)


def test_report_context_middleware_no_report_id_passthrough():
    """report_id=None 时直接透传"""
    from app.ai.agents.chat_graph import ReportContextMiddleware

    mw = ReportContextMiddleware(report_id=None)
    request = MagicMock()
    handler = MagicMock(return_value="response")
    result = mw.wrap_model_call(request, handler)
    assert result == "response"
