from unittest.mock import patch, MagicMock

from pydantic import BaseModel


def test_interp_state_fields():
    """InterpState 含必需字段"""
    from app.ai.agents.interp_graph import InterpState
    assert "indicators" in InterpState.__annotations__
    assert "judgments" in InterpState.__annotations__
    assert "abnormal_indicators" in InterpState.__annotations__
    assert "agent_explanations" in InterpState.__annotations__
    assert "overall_level" in InterpState.__annotations__


def test_interp_batch_result_is_pydantic():
    """InterpBatchResult 是 Pydantic BaseModel（非 TypedDict）"""
    from app.ai.agents.interp_graph import InterpBatchResult
    assert issubclass(InterpBatchResult, BaseModel)
    inst = InterpBatchResult(items=[])
    assert inst.items == []


def test_build_interp_graph_returns_compiled():
    """build_interp_graph 返回可编译的图"""
    with patch("app.ai.agents.interp_graph.get_chat_model") as mock_model:
        mock_model.return_value = MagicMock()
        from app.ai.agents.interp_graph import build_interp_graph
        graph = build_interp_graph("H001", MagicMock())
        assert graph is not None


def test_build_interp_agent_returns_compiled():
    """build_interp_agent 返回非 None（create_agent 产物）"""
    with patch("app.ai.agents.interp_graph.get_chat_model") as mock_model:
        mock_model.return_value = MagicMock()
        from app.ai.agents.interp_graph import build_interp_agent
        agent = build_interp_agent()
        assert agent is not None


def test_agent_batch_empty_abnormal_returns_empty():
    """abnormal_indicators 为空时 agent_batch 返回空结果"""
    from app.ai.agents.interp_graph import _agent_batch
    result = _agent_batch(
        {"abnormal_indicators": [], "hospital_id": "H001", "report_id": 1},
        MagicMock(),
        MagicMock(),
    )
    assert result == {"agent_explanations": {}, "knowledge_refs": {}}


def test_map_structured_to_explanations():
    """_map_structured_to_explanations 正确映射结构化输出到 explanations/refs"""
    from app.ai.agents.interp_graph import (
        _map_structured_to_explanations, InterpBatchItem, InterpBatchResult,
    )
    structured = InterpBatchResult(items=[
        InterpBatchItem(indicator_id=10, explanation="解读A", suggestion="建议A", knowledge_ref_ids=[101]),
        InterpBatchItem(indicator_id=20, explanation="解读B", suggestion="建议B", knowledge_ref_ids=[201, 202]),
    ])
    knowledge_results = {
        101: {"entry_id": 101, "title": "知识A"},
        201: {"entry_id": 201, "title": "知识B1"},
        202: {"entry_id": 202, "title": "知识B2"},
    }
    abnormal = [{"indicator_id": 10}, {"indicator_id": 20}, {"indicator_id": 30}]

    explanations, mapped_refs = _map_structured_to_explanations(structured, knowledge_results, abnormal)

    assert explanations[10] == {"explanation": "解读A", "suggestion": "建议A"}
    assert explanations[20] == {"explanation": "解读B", "suggestion": "建议B"}
    # 30 未在结构化输出，补全为空
    assert explanations[30] == {"explanation": "", "suggestion": ""}
    # refs 是 knowledge_ref_ids 反查
    assert mapped_refs[10] == [{"entry_id": 101, "title": "知识A"}]
    assert mapped_refs[20] == [{"entry_id": 201, "title": "知识B1"}, {"entry_id": 202, "title": "知识B2"}]
    # 30 fallback 用全部 knowledge_results
    assert len(mapped_refs[30]) == 3


def test_interp_knowledge_middleware_extracts_refs_dict():
    """InterpKnowledgeMiddleware 从 search_knowledge 结果提取 {entry_id: ref}"""
    import json
    from langchain.messages import ToolMessage
    from langgraph.types import Command
    from app.ai.agents.interp_graph import InterpKnowledgeMiddleware

    mw = InterpKnowledgeMiddleware()
    tool_msg = ToolMessage(
        content=json.dumps([{"entry_id": 101, "title": "知识A", "content": "...", "score": 0.9}]),
        tool_call_id="call_1",
    )
    request = MagicMock()
    request.tool_call = {"name": "search_knowledge", "id": "call_1", "args": {}}
    handler = MagicMock(return_value=tool_msg)

    result = mw.wrap_tool_call(request, handler)
    assert isinstance(result, Command)
    assert result.update["knowledge_results"] == {101: {"entry_id": 101, "title": "知识A"}}


def _build_test_graph(mock_build):
    """测试辅助：构造最小图调 agent_batch"""
    pass
