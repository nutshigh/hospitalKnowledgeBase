from unittest.mock import patch, MagicMock

import pytest


def _make_runtime(ctx):
    """Helper: create a real ToolRuntime with minimal required fields."""
    from langchain.tools import ToolRuntime
    return ToolRuntime(
        state={},
        context=ctx,
        config={"configurable": {}},
        stream_writer=MagicMock(),
        tool_call_id="test-call-id",
        store=None,
        tools=[],
    )


def test_chat_tools_contains_six_names():
    """CHAT_TOOLS 包含 6 个工具，名称正确"""
    from app.ai.agents.tools import CHAT_TOOLS
    names = {t.name for t in CHAT_TOOLS}
    assert names == {
        "search_knowledge", "get_report_indicators", "get_report_summary",
        "get_user_history_reports", "get_indicator_history", "get_triage_rules",
    }


def test_interp_tools_contains_two_names():
    """INTERP_TOOLS 只含 search_knowledge + get_triage_rules"""
    from app.ai.agents.tools import INTERP_TOOLS
    names = {t.name for t in INTERP_TOOLS}
    assert names == {"search_knowledge", "get_triage_rules"}


def test_agent_context_dataclass():
    """AgentContext 包含 hospital_id/report_id/user_id/name"""
    from app.ai.agents.tools import AgentContext
    ctx = AgentContext(hospital_id="H001")
    assert ctx.hospital_id == "H001"
    assert ctx.report_id is None
    assert ctx.user_id is None
    assert ctx.name is None
    ctx2 = AgentContext(hospital_id="H001", report_id=5, user_id="123456", name="张三")
    assert ctx2.report_id == 5 and ctx2.user_id == "123456"
    assert ctx2.name == "张三"


def test_search_knowledge_uses_context_hospital_id():
    """search_knowledge 使用 runtime.context 的 hospital_id"""
    with patch("app.ai.agents.tools.ai_rag") as mock_rag:
        from app.modules.knowledge.schemas import SearchResult
        mock_rag.search.return_value = [SearchResult(
            entry_id=1, title="t", content="c", category_id=2, score=0.9
        )]
        from app.ai.agents.tools import search_knowledge, AgentContext

        ctx = AgentContext(hospital_id="H002")
        runtime = _make_runtime(ctx)

        result = search_knowledge.invoke(
            {"query": "血糖", "runtime": runtime}
        )
        assert isinstance(result, list)
        assert result[0]["entry_id"] == 1
        mock_rag.search.assert_called_once_with("H002", "血糖", category_ids=None, top_k=None)


def test_get_report_indicators_uses_context_db():
    """get_report_indicators 通过 AgentContext.report_id 注入并查询"""
    from app.ai.agents.tools import get_report_indicators, AgentContext

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = [
        (1, "ALT", "ALT", "85", "U/L", "0", "40")
    ]
    ctx = AgentContext(hospital_id="H001", report_id=7)
    runtime = _make_runtime(ctx)

    with patch("app.ai.agents.tools.get_session", return_value=mock_db):
        result = get_report_indicators.invoke({"runtime": runtime})
    assert len(result) == 1
    assert result[0]["item_name"] == "ALT"
    mock_db.execute.assert_called_once()
    mock_db.close.assert_called_once()
    # 确认 SQL 参数用的是 context.report_id
    args, kwargs = mock_db.execute.call_args
    params = kwargs.get("params") if "params" in kwargs else (args[1] if len(args) > 1 else None)
    assert params == {"rid": 7}


def test_get_report_indicators_missing_report_id_returns_error():
    """无 report_id 时返回错误提示而非抛出"""
    from app.ai.agents.tools import get_report_indicators, AgentContext
    ctx = AgentContext(hospital_id="H001")
    runtime = _make_runtime(ctx)
    result = get_report_indicators.invoke({"runtime": runtime})
    assert isinstance(result, list)
    assert "error" in result[0]


def test_make_tools_removed():
    """make_tools 函数已删除"""
    import app.ai.agents.tools as tools_mod
    assert not hasattr(tools_mod, "make_tools")


def test_get_user_history_reports_dual_filter():
    """get_user_history_reports 按 user_id + name 双条件过滤"""
    from app.ai.agents.tools import get_user_history_reports, AgentContext

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []
    ctx = AgentContext(hospital_id="H001", user_id="123456", name="张三")
    runtime = _make_runtime(ctx)

    with patch("app.ai.agents.tools.get_session", return_value=mock_db):
        result = get_user_history_reports.invoke({"runtime": runtime, "limit": 5})

    assert result == []
    args, kwargs = mock_db.execute.call_args
    sql = args[0].text
    assert "r.user_id = :uid" in sql
    assert "r.name = :nm" in sql
    params = kwargs.get("params") if "params" in kwargs else (args[1] if len(args) > 1 else None)
    assert params == {"uid": "123456", "nm": "张三", "lim": 5}


def test_get_user_history_reports_missing_name_returns_error():
    """缺少 name 时返回错误而非查询"""
    from app.ai.agents.tools import get_user_history_reports, AgentContext

    ctx = AgentContext(hospital_id="H001", user_id="123456")
    runtime = _make_runtime(ctx)

    result = get_user_history_reports.invoke({"runtime": runtime})

    assert result == [{"error": "无法识别当前用户"}]


def test_get_indicator_history_dual_filter():
    """get_indicator_history 按 user_id + name 双条件过滤"""
    from app.ai.agents.tools import get_indicator_history, AgentContext

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []
    ctx = AgentContext(hospital_id="H001", user_id="123456", name="张三")
    runtime = _make_runtime(ctx)

    with patch("app.ai.agents.tools.get_session", return_value=mock_db):
        result = get_indicator_history.invoke({"item_name": "血糖", "runtime": runtime})

    assert result == []
    args, kwargs = mock_db.execute.call_args
    sql = args[0].text
    assert "ri.user_id = :uid" in sql
    assert "ri.name = :nm" in sql
    params = kwargs.get("params") if "params" in kwargs else (args[1] if len(args) > 1 else None)
    assert params == {"uid": "123456", "nm": "张三", "name": "血糖"}


def test_get_indicator_history_missing_name_returns_error():
    """缺少 name 时返回错误而非查询"""
    from app.ai.agents.tools import get_indicator_history, AgentContext

    ctx = AgentContext(hospital_id="H001", user_id="123456")
    runtime = _make_runtime(ctx)

    result = get_indicator_history.invoke({"item_name": "血糖", "runtime": runtime})

    assert result == [{"error": "无法识别当前用户"}]
