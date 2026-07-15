"""chat_planner 单元测试。

测试 run_planner（结构化输出决策）和 execute_plan（Python 执行工具）。
所有 LLM 调用和环境依赖均被 mock，无需 vLLM / Neo4j / Milvus 在线。
"""
import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock

from langchain.messages import HumanMessage, AIMessage


def test_chat_plan_schema_defaults():
    """ChatPlan 默认值正确"""
    from app.ai.agents.chat_planner import ChatPlan, PlannedToolCall

    plan = ChatPlan()
    assert plan.need_tools is False
    assert plan.tool_calls == []
    assert plan.summary == ""

    tc = PlannedToolCall(tool="search_knowledge", query="高血压")
    assert tc.tool == "search_knowledge"
    assert tc.query == "高血压"
    assert tc.item_name is None


@pytest.mark.asyncio
async def test_run_planner_returns_plan_with_tools():
    """run_planner 对医学问题返回带 tool_calls 的计划"""
    from app.ai.agents.chat_planner import run_planner, ChatPlan, PlannedToolCall

    fake_model = MagicMock()
    fake_model.max_tokens = 4096
    fake_model.temperature = 0.1
    fake_model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=ChatPlan(
        need_tools=True,
        tool_calls=[PlannedToolCall(tool="search_knowledge", query="高血压 并发症")],
    ))

    with patch("app.ai.agents.chat_planner.get_chat_model", return_value=fake_model):
        plan = await run_planner("H001", [], "高血压有什么并发症", None, 4)

    assert plan.need_tools is True
    assert len(plan.tool_calls) == 1
    assert plan.tool_calls[0].tool == "search_knowledge"
    assert plan.tool_calls[0].query == "高血压 并发症"


@pytest.mark.asyncio
async def test_run_planner_returns_empty_plan_for_greeting():
    """run_planner 对问候返回空计划"""
    from app.ai.agents.chat_planner import run_planner, ChatPlan

    fake_model = MagicMock()
    fake_model.with_structured_output.return_value.ainvoke = AsyncMock(return_value=ChatPlan(
        need_tools=False, tool_calls=[], summary="纯问候",
    ))

    with patch("app.ai.agents.chat_planner.get_chat_model", return_value=fake_model):
        plan = await run_planner("H001", [], "你好", None, 4)

    assert plan.need_tools is False
    assert plan.tool_calls == []


@pytest.mark.asyncio
async def test_run_planner_handles_error():
    """run_planner 在模型异常时返回空计划"""
    from app.ai.agents.chat_planner import run_planner, ChatPlan

    fake_model = MagicMock()
    fake_model.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=RuntimeError("model down"))

    with patch("app.ai.agents.chat_planner.get_chat_model", return_value=fake_model):
        plan = await run_planner("H001", [], "高血压", None, 4)

    assert plan.need_tools is False
    assert "planner error" in plan.summary


def test_execute_plan_search_knowledge():
    """execute_plan 执行 search_knowledge 返回 refs 和 context"""
    from app.ai.agents.chat_planner import execute_plan, ChatPlan, PlannedToolCall
    from app.ai.agents.tools import AgentContext
    from app.ai.rag.types import SearchResult

    plan = ChatPlan(
        need_tools=True,
        tool_calls=[PlannedToolCall(tool="search_knowledge", query="高血压 并发症")],
    )
    ctx = AgentContext(hospital_id="H001", report_id=None, user_id=4)

    mock_results = [
        SearchResult(entry_id=1, title="高血压并发症", content="心脏病、脑卒中...",
                     score=0.9, source="document"),
        SearchResult(entry_id=None, title="高血压", content="实体: 高血压...",
                     score=0.8, source="knowledge_graph"),
    ]
    with patch("app.ai.rag.search", return_value=mock_results):
        refs, context = execute_plan(plan, ctx)

    assert len(refs) == 2
    assert refs[0]["entry_id"] == 1
    assert refs[0]["title"] == "高血压并发症"
    assert refs[0]["source"] == "document"
    assert refs[1]["entry_id"] is None
    assert refs[1]["source"] == "knowledge_graph"
    assert "高血压 并发症" in context
    assert "高血压并发症" in context


def test_execute_plan_get_report_indicators_no_report():
    """execute_plan 对 get_report_indicators 无报告时返回提示"""
    from app.ai.agents.chat_planner import execute_plan, ChatPlan, PlannedToolCall
    from app.ai.agents.tools import AgentContext

    plan = ChatPlan(
        need_tools=True,
        tool_calls=[PlannedToolCall(tool="get_report_indicators")],
    )
    ctx = AgentContext(hospital_id="H001", report_id=None, user_id=4)

    refs, context = execute_plan(plan, ctx)

    assert refs == []
    assert "未关联报告" in context


def test_execute_plan_empty_plan():
    """execute_plan 对空计划返回空结果"""
    from app.ai.agents.chat_planner import execute_plan, ChatPlan
    from app.ai.agents.tools import AgentContext

    plan = ChatPlan(need_tools=False, tool_calls=[])
    ctx = AgentContext(hospital_id="H001", report_id=None, user_id=4)

    refs, context = execute_plan(plan, ctx)

    assert refs == []
    assert context == ""


def test_execute_plan_unknown_tool_skipped():
    """execute_plan 对未知工具名跳过并不打断"""
    from app.ai.agents.chat_planner import execute_plan, ChatPlan, PlannedToolCall
    from app.ai.agents.tools import AgentContext

    plan = ChatPlan(
        need_tools=True,
        tool_calls=[
            PlannedToolCall(tool="nonexistent_tool"),
            PlannedToolCall(tool="get_triage_rules"),
        ],
    )
    ctx = AgentContext(hospital_id="H001", report_id=None, user_id=4)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []
    mock_db.close = MagicMock()
    with patch("app.ai.agents.chat_planner.get_session", return_value=mock_db):
        refs, context = execute_plan(plan, ctx)

    assert refs == []
    assert "triaje_rule" not in context  # mock 返回空
    assert "get_triage_rules" in context  # 但至少有这个工具的标题


def test_build_answer_system_prompt_with_context():
    """_build_answer_system_prompt 注入检索结果"""
    from app.ai.agents.chat_graph import _build_answer_system_prompt

    prompt = _build_answer_system_prompt("### search_knowledge\n高血压知识...", report_id=42)

    assert "检索结果" in prompt
    assert "高血压知识" in prompt
    assert "42" in prompt


def test_build_answer_system_prompt_empty_context():
    """_build_answer_system_prompt 无检索结果时标注提示"""
    from app.ai.agents.chat_graph import _build_answer_system_prompt

    prompt = _build_answer_system_prompt("", report_id=None)

    assert "无检索结果" in prompt
    assert "检索结果" in prompt


def test_build_answer_system_prompt_no_report_id():
    """_build_answer_system_prompt 不加 report_id 时无多余输出"""
    from app.ai.agents.chat_graph import _build_answer_system_prompt

    prompt = _build_answer_system_prompt("some context", None)
    assert "关联报告" not in prompt