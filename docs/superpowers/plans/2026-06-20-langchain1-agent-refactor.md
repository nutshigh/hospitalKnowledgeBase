# LangChain 1.0 Agent 改造实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `ai/agents/` 的手写工具循环/消息管理改造为 LangChain 1.0 的 `create_agent` + 中间件范式，删掉手写 tool_node/should_continue/正则解析/闭包绑定。

**Architecture:** `tools.py` 闭包改为模块级 `@tool` + `ToolRuntime[AgentContext]`；`chat_graph.py` 手写 StateGraph 换成 `create_agent` + `KnowledgeRefsMiddleware`(wrap_tool_call) + `ReportContextMiddleware`(wrap_model_call) + `stream_events(version="v3")`；`interp_graph.py` 外层 StateGraph 保留，`agent_batch` 节点换成 `create_agent` 子图 + `response_format=ToolStrategy(InterpBatchResult)`。对外契约不变。

**Tech Stack:** Python 3.12, LangChain 1.0（`create_agent`/`AgentMiddleware`/`ToolStrategy`/`ToolRuntime`），LangGraph 1.2，pytest，SQLAlchemy

**Spec:** `docs/superpowers/specs/2026-06-20-langchain1-agent-refactor-design.md`

## Global Constraints

- Python >=3.12，包管理用 uv
- 依赖主版本升级：`langchain>=1.0,<2.0`、`langchain-core>=1.4.7,<2.0`、`langchain-openai>=1.0,<2.0`、`langgraph>=1.2.5,<1.3`
- LlamaIndex 层不动（`ai/rag/*`、`ai/llm.py`、`ai/config.py`）
- modules 层调用契约不变（`run_chat_agent`/`run_interpretation_agent` 签名不变）
- SSE 事件类型不变（`tool_status`/`token`/`done`/`error`）
- MySQL 表结构不变
- 测试用 pytest + unittest.mock，外部依赖 mock
- `CHAT_SYSTEM_PROMPT`/`INTERP_SYSTEM_PROMPT` 文本不变
- `AGENT_MAX_ITERATIONS` 配置值不变（映射到 `recursion_limit = AGENT_MAX_ITERATIONS * 2`）
- `_session_locks` 并发控制保留
- 中文 system prompt 不变

---

## File Structure

### 修改文件

| 文件 | 职责 |
|------|------|
| `backend/pyproject.toml` | langchain 依赖主版本升级 |
| `backend/app/ai/agents/tools.py` | 闭包 → 模块级 `@tool` + `ToolRuntime[AgentContext]` + `CHAT_TOOLS`/`INTERP_TOOLS` 常量 |
| `backend/app/ai/agents/chat_graph.py` | 手写 StateGraph → `create_agent` + `KnowledgeRefsMiddleware` + `ReportContextMiddleware` + `stream_events(v3)` |
| `backend/app/ai/agents/interp_graph.py` | `agent_batch` 节点换成 `create_agent` 子图 + `ToolStrategy(InterpBatchResult)`，外层 StateGraph 不变 |
| `backend/app/ai/agents/__init__.py` | 导出调整：删 `make_tools`，加 `AgentContext` |
| `backend/tests/ai/agents/test_tools.py` | 适配 ToolRuntime + 新增 context 注入测试 |
| `backend/tests/ai/agents/test_chat_graph.py` | 适配 create_agent + 新增中间件测试 |
| `backend/tests/ai/agents/test_interp_graph.py` | 适配 create_agent + 新增结构化输出测试 |

### 不动的文件

`ai/rag/*`、`ai/llm.py`、`ai/config.py`、`modules/chat/*`、`modules/interpretation/*`、`app/config.py`、所有 models、`test_chat_migration.py`、`test_interp_migration.py`

---

## Task 1: 依赖升级

**Files:**
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `langchain>=1.0` 安装后可用 `from langchain.agents import create_agent` 等 1.0 API

- [ ] **Step 1: 编辑 pyproject.toml 替换 langchain 依赖**

编辑 `backend/pyproject.toml`，将这 3 行：
```toml
    "langchain-core>=0.3,<0.4",
    "langchain-openai>=0.2,<0.3",
    "langgraph>=0.2,<0.3",
```
替换为：
```toml
    "langchain>=1.0,<2.0",
    "langchain-core>=1.4.7,<2.0",
    "langchain-openai>=1.0,<2.0",
    "langgraph>=1.2.5,<1.3",
```

- [ ] **Step 2: 安装新依赖**

Run: `cd backend && uv sync`
Expected: 成功安装 `langchain` 1.x 及升级 `langchain-core`/`langgraph`/`langchain-openai` 到 1.x，无冲突

- [ ] **Step 3: 验证 1.0 API 可导入**

Run:
```bash
cd backend && uv run python -c "from langchain.agents import create_agent, AgentState; from langchain.agents.middleware import AgentMiddleware; from langchain.agents.structured_output import ToolStrategy; from langchain.tools import tool, ToolRuntime; from langchain.tools.tool_node import ToolCallRequest; from langchain.messages import SystemMessage, ToolMessage, HumanMessage, AIMessage; from langgraph.types import Command; print('all 1.0 imports ok')"
```
Expected: 输出 `all 1.0 imports ok`

- [ ] **Step 4: 验证现有测试不因升级而 import 崩溃**

Run: `cd backend && uv run pytest tests/ai/ -v --collect-only`
Expected: 能收集测试用例（可能因 API 变化 FAIL，但不应 import 错误中断收集）

- [ ] **Step 5: Commit**

```bash
cd backend && git add pyproject.toml && git commit -m "build: upgrade to langchain 1.0 + langgraph 1.2 for create_agent"
```

---

## Task 2: tools.py — AgentContext + ToolRuntime + 模块级工具

**Files:**
- Modify: `backend/app/ai/agents/tools.py`
- Modify: `backend/tests/ai/agents/test_tools.py`

**Interfaces:**
- Produces: `AgentContext` dataclass、模块级 `search_knowledge`/`get_report_indicators`/`get_report_summary`/`get_user_history_reports`/`get_indicator_history`/`get_triage_rules` 工具、`CHAT_TOOLS`/`INTERP_TOOLS` 常量
- Deletes: `make_tools()` 函数

- [ ] **Step 1: 写失败测试**

替换 `backend/tests/ai/agents/test_tools.py` 全部内容为：

```python
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest


def test_chat_tools_contains_six_names():
    """CHAT_TOOLS 含 6 个工具，名称正确"""
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
    """AgentContext 含 hospital_id 和 db_session"""
    from app.ai.agents.tools import AgentContext
    ctx = AgentContext(hospital_id="H001", db_session=MagicMock())
    assert ctx.hospital_id == "H001"


def test_search_knowledge_uses_context_hospital_id():
    """search_knowledge 从 runtime.context 取 hospital_id"""
    with patch("app.ai.agents.tools.ai_rag") as mock_rag:
        from app.modules.knowledge.schemas import SearchResult
        mock_rag.search.return_value = [SearchResult(
            entry_id=1, title="t", content="c", category_id=2, score=0.9
        )]
        from app.ai.agents.tools import search_knowledge, AgentContext
        from langchain.tools import ToolRuntime

        ctx = AgentContext(hospital_id="H002", db_session=MagicMock())
        runtime = MagicMock(spec=ToolRuntime)
        runtime.context = ctx

        result = search_knowledge.invoke(
            {"query": "血糖", "runtime": runtime}
        )
        assert isinstance(result, list)
        assert result[0]["entry_id"] == 1
        mock_rag.search.assert_called_once_with("H002", "血糖", None, None)


def test_get_report_indicators_uses_context_db():
    """get_report_indicators 从 runtime.context.db_session 执行查询"""
    from app.ai.agents.tools import get_report_indicators, AgentContext
    from langchain.tools import ToolRuntime

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = [
        (1, "ALT", "ALT", "85", "U/L", "0", "40")
    ]
    ctx = AgentContext(hospital_id="H001", db_session=mock_db)
    runtime = MagicMock(spec=ToolRuntime)
    runtime.context = ctx

    result = get_report_indicators.invoke({"report_id": 1, "runtime": runtime})
    assert len(result) == 1
    assert result[0]["item_name"] == "ALT"
    mock_db.execute.assert_called_once()


def test_make_tools_removed():
    """make_tools 函数已删除"""
    import app.ai.agents.tools as tools_mod
    assert not hasattr(tools_mod, "make_tools")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/agents/test_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'CHAT_TOOLS'` 或 `AgentContext`

- [ ] **Step 3: 重写 tools.py**

替换 `backend/app/ai/agents/tools.py` 全部内容为：

```python
from dataclasses import dataclass
from typing import List, Optional

from langchain.tools import tool, ToolRuntime, BaseTool
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai import rag as ai_rag


@dataclass
class AgentContext:
    """工具运行时上下文，通过 agent.invoke(context=...) 注入"""
    hospital_id: str
    db_session: Session


@tool
def search_knowledge(
    query: str,
    category_ids: Optional[List[int]] = None,
    top_k: Optional[int] = None,
    runtime: ToolRuntime[AgentContext],
) -> list[dict]:
    """搜索医学知识库，返回相关知识条目。用于查找指标解读、疾病知识、健康建议等医学信息。
    Args:
        query: 搜索查询，如"空腹血糖偏高"或"ALT 升高原因"
        category_ids: 可选，限定知识分类 ID 列表
        top_k: 可选，返回条数上限
    Returns:
        知识条目列表，每项含 entry_id/title/content/score
    """
    ctx = runtime.context
    results = ai_rag.search(ctx.hospital_id, query, category_ids=category_ids, top_k=top_k)
    return [{"entry_id": r.entry_id, "title": r.title, "content": r.content, "score": r.score} for r in results]


@tool
def get_report_indicators(report_id: int, runtime: ToolRuntime[AgentContext]) -> list[dict]:
    """获取体检报告的所有结构化指标数据。
    Args:
        report_id: 报告 ID
    Returns:
        指标列表，每项含 item_name/result_value/unit/ref_range_low/ref_range_high
    """
    db = runtime.context.db_session
    rows = db.execute(
        text("SELECT id, item_name, item_name_standard, result_value, unit, "
             "ref_range_low, ref_range_high FROM report_indicator WHERE report_id = :rid ORDER BY id"),
        {"rid": report_id},
    ).fetchall()
    return [{"id": r[0], "item_name": r[1], "item_name_standard": r[2],
             "result_value": r[3], "unit": r[4],
             "ref_range_low": r[5], "ref_range_high": r[6]} for r in rows]


@tool
def get_report_summary(report_id: int, runtime: ToolRuntime[AgentContext]) -> dict:
    """获取报告概览信息（报告日期、整体判定、红黄绿计数）。
    Args:
        report_id: 报告 ID
    Returns:
        含 report_date/overall_level/red_count/yellow_count/green_count 的 dict
    """
    db = runtime.context.db_session
    row = db.execute(
        text("SELECT r.report_date, r.name, i.overall_level, i.red_count, i.yellow_count, i.green_count "
             "FROM report_info r LEFT JOIN report_interpretation i ON i.report_id = r.id "
             "WHERE r.id = :rid"),
        {"rid": report_id},
    ).fetchone()
    if not row:
        return {}
    return {"report_date": str(row[0]) if row[0] else None, "name": row[1],
            "overall_level": row[2], "red_count": row[3],
            "yellow_count": row[4], "green_count": row[5]}


@tool
def get_user_history_reports(user_id: int, limit: int = 5, runtime: ToolRuntime[AgentContext]) -> list[dict]:
    """获取用户历年体检报告概览，用于趋势对比。
    Args:
        user_id: 用户 ID
        limit: 返回条数，默认 5
    Returns:
         报告列表，每项含 report_id/report_date/overall_level
    """
    db = runtime.context.db_session
    rows = db.execute(
        text("SELECT r.id, r.report_date, i.overall_level "
             "FROM report_info r LEFT JOIN report_interpretation i ON i.report_id = r.id "
             "WHERE r.user_id = :uid ORDER BY r.report_date DESC LIMIT :lim"),
        {"uid": user_id, "lim": limit},
    ).fetchall()
    return [{"report_id": r[0], "report_date": str(r[1]) if r[1] else None,
             "overall_level": r[2]} for r in rows]


@tool
def get_indicator_history(user_id: int, item_name: str, runtime: ToolRuntime[AgentContext]) -> list[dict]:
    """获取用户某指标的历史数值，用于趋势研判。
    Args:
        user_id: 用户 ID
        item_name: 指标名称
    Returns:
        历史数值列表，每项含 date/value/unit
    """
    db = runtime.context.db_session
    rows = db.execute(
        text("SELECT ri.report_date, ind.result_value, ind.unit "
             "FROM report_indicator ind "
             "JOIN report_info ri ON ind.report_id = ri.id "
             "WHERE ri.user_id = :uid AND ind.item_name = :name "
             "ORDER BY ri.report_date ASC"),
        {"uid": user_id, "name": item_name},
    ).fetchall()
    return [{"date": str(r[0]) if r[0] else None, "value": r[1], "unit": r[2]} for r in rows]


@tool
def get_triage_rules(runtime: ToolRuntime[AgentContext]) -> list[dict]:
    """获取当前生效的三色分级规则，了解哪些指标阈值会被判定为红区/黄区。
    Returns:
        规则列表，每项含 rule_name/indicator_code/conditions/color_level
    """
    db = runtime.context.db_session
    rows = db.execute(
        text("SELECT rule_name, indicator_code, conditions, color_level "
             "FROM triage_rule WHERE is_active = 1 ORDER BY priority"),
    ).fetchall()
    return [{"rule_name": r[0], "indicator_code": r[1],
             "conditions": r[2], "color_level": r[3]} for r in rows]


CHAT_TOOLS: list[BaseTool] = [
    search_knowledge, get_report_indicators, get_report_summary,
    get_user_history_reports, get_indicator_history, get_triage_rules,
]

INTERP_TOOLS: list[BaseTool] = [search_knowledge, get_triage_rules]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_tools.py -v`
Expected: PASS（6 个测试全过）

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/agents/tools.py tests/ai/agents/test_tools.py && git commit -m "refactor: tools.py to module-level @tool with ToolRuntime[AgentContext]"
```

---

## Task 3: chat_graph.py — create_agent + 中间件

**Files:**
- Modify: `backend/app/ai/agents/chat_graph.py`
- Modify: `backend/tests/ai/agents/test_chat_graph.py`

**Interfaces:**
- Produces: `ChatAgentState`、`KnowledgeRefsMiddleware`、`ReportContextMiddleware`、`build_chat_agent(report_id)`、`run_chat_agent(...)`（签名不变）
- Deletes: `ChatState`、`build_chat_graph(hospital_id, db)`

- [ ] **Step 1: 写失败测试**

替换 `backend/tests/ai/agents/test_chat_graph.py` 全部内容为：

```python
import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock

from langchain.messages import ToolMessage
from langgraph.types import Command


def test_chat_agent_state_has_knowledge_refs():
    """ChatAgentState 含 knowledge_refs 字段"""
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
        content=json.dumps([{"entry_id": 1, "title": "血糖知识", "content": "...", "score": 0.9}]),
        tool_call_id="call_1",
    )
    request = MagicMock()
    request.tool_call = {"name": "search_knowledge", "id": "call_1", "args": {}}
    handler = MagicMock(return_value=tool_msg)

    result = mw.wrap_tool_call(request, handler)
    assert isinstance(result, Command)
    assert result.update["knowledge_refs"] == [{"entry_id": 1, "title": "血糖知识"}]


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
    from langchain.messages import SystemMessage

    mw = ReportContextMiddleware(report_id=42)
    request = MagicMock()
    request.system_message = SystemMessage(content="你是助手")
    handler = MagicMock(return_value="response")

    mw.wrap_model_call(request, handler)
    handler.assert_called_once()
    passed = handler.call_args[0][0]
    # 追加后 system_message content_blocks 应含 report_id 42 的文本
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/agents/test_chat_graph.py -v`
Expected: FAIL — `ImportError: cannot import name 'ChatAgentState'` 或 `build_chat_agent`

- [ ] **Step 3: 重写 chat_graph.py**

替换 `backend/app/ai/agents/chat_graph.py` 全部内容为：

```python
import json
from typing import AsyncIterator, Optional

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command
from typing_extensions import Annotated, NotRequired

from app.ai.llm import get_chat_model
from app.ai.agents.tools import AgentContext, CHAT_TOOLS
from app.config import settings

CHAT_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，为体检者提供易懂的健康咨询。

规则:
1. 基于报告数据和知识库回答，不编造信息
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"
6. 用户未关联报告时，引导其先上传报告以获取更精准建议

你有以下工具可用：
- search_knowledge: 搜索医学知识库
- get_report_indicators: 获取报告指标数据
- get_report_summary: 获取报告概览
- get_user_history_reports: 获取历年报告
- get_indicator_history: 获取指标历史趋势
- get_triage_rules: 获取三色分级规则

优先用工具获取信息，不要凭空回答。"""


def _accumulate_refs(existing: list, new: list) -> list:
    return existing + new


class ChatAgentState(AgentState):
    knowledge_refs: NotRequired[Annotated[list[dict], _accumulate_refs]]


def _extract_refs_from_tool_result(result) -> list[dict]:
    """从 ToolMessage 或 Command 解析 search_knowledge 返回的 refs"""
    msgs = []
    if isinstance(result, Command):
        msgs = (result.update or {}).get("messages", [])
    else:
        msgs = [result]
    refs = []
    for m in msgs:
        if isinstance(m, ToolMessage):
            try:
                data = json.loads(m.content)
                if isinstance(data, list):
                    refs.extend({"entry_id": r.get("entry_id"), "title": r.get("title")} for r in data)
            except (json.JSONDecodeError, TypeError):
                pass
    return refs


class KnowledgeRefsMiddleware(AgentMiddleware):
    """拦截 search_knowledge，把 {entry_id,title} 累积进 state.knowledge_refs"""

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        result = handler(request)
        if request.tool_call["name"] == "search_knowledge":
            refs = _extract_refs_from_tool_result(result)
            if refs:
                if isinstance(result, Command):
                    update = dict(result.update or {})
                    update["knowledge_refs"] = refs
                    return Command(update=update)
                return Command(update={"knowledge_refs": refs})
        return result


class ReportContextMiddleware(AgentMiddleware):
    """把 report_id 上下文追加到 system_message"""

    def __init__(self, report_id: Optional[int]):
        super().__init__()
        self.report_id = report_id

    def wrap_model_call(self, request, handler):
        if self.report_id:
            extra_text = f"\n\n当前会话关联的报告 ID 是 {self.report_id}，用户提问时可用 get_report_indicators 获取详细指标。"
            new_content = list(request.system_message.content_blocks) + [{"type": "text", "text": extra_text}]
            new_sys = SystemMessage(content=new_content)
            return handler(request.override(system_message=new_sys))
        return handler(request)


def build_chat_agent(report_id: Optional[int]):
    """构造 chat Agent（create_agent + 中间件）。每请求新建因 middleware 带状态。"""
    model = get_chat_model(streaming=True)
    return create_agent(
        model=model,
        tools=CHAT_TOOLS,
        system_prompt=CHAT_SYSTEM_PROMPT,
        middleware=[
            KnowledgeRefsMiddleware(),
            ReportContextMiddleware(report_id),
        ],
        state_schema=ChatAgentState,
    )


MAX_HISTORY_ROUNDS = 20

_session_locks: set[int] = set()


async def run_chat_agent(
    hospital_id: str,
    db,
    session,
    user_message: str,
    user_id: int,
) -> AsyncIterator[dict]:
    """运行 chat Agent，yield SSE 事件 dict。
    事件类型：tool_status / token / done / error
    """
    from app.modules.chat import service as chat_service

    session_id = session.id
    if session_id in _session_locks:
        yield {"event": "error", "data": {"message": "正在处理上一条消息，请稍候"}}
        return
    _session_locks.add(session_id)

    try:
        chat_service.save_message(db, session_id, "user", user_message)

        history = chat_service.get_messages(db, session_id)
        history_msgs = [
            (HumanMessage(content=m.content) if m.role == "user"
             else AIMessage(content=m.content))
            for m in history[-MAX_HISTORY_ROUNDS * 2:-1]
        ]

        agent = build_chat_agent(session.report_id)
        ctx = AgentContext(hospital_id=hospital_id, db_session=db)
        inputs = {"messages": history_msgs + [HumanMessage(content=user_message)]}
        config = {"recursion_limit": settings.AGENT_MAX_ITERATIONS * 2}

        final_response = ""
        final_state = None
        async for event in agent.stream_events(inputs, version="v3", config=config, context=ctx):
            kind = event.get("event")
            if kind == "on_tool_start":
                yield {"event": "tool_status", "data": {
                    "tool": event.get("name", ""), "status": "start"}}
            elif kind == "on_tool_end":
                yield {"event": "tool_status", "data": {
                    "tool": event.get("name", ""), "status": "end"}}
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    if not hasattr(chunk, "tool_call_chunks") or not chunk.tool_call_chunks:
                        final_response += chunk.content
                        yield {"event": "token", "data": {"content": chunk.content}}
            elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                final_state = event.get("data", {}).get("output")

        refs = (final_state or {}).get("knowledge_refs", [])

        msg = chat_service.save_message(
            db, session_id, "assistant", final_response, knowledge_refs=refs or None
        )

        if not session.title:
            title = user_message[:50] + ("..." if len(user_message) > 50 else "")
            db.query(type(session)).filter(type(session).id == session_id).update({"title": title})
            db.commit()

        yield {"event": "done", "data": {"message_id": msg.id}}
    except Exception as e:
        yield {"event": "error", "data": {"message": f"AI 响应失败: {e}"}}
    finally:
        _session_locks.discard(session_id)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_chat_graph.py -v`
Expected: PASS（8 个测试全过）

- [ ] **Step 5: 验证 chat_migration 测试仍通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_chat_migration.py -v`
Expected: PASS（签名不变，mock 不受影响）

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/ai/agents/chat_graph.py tests/ai/agents/test_chat_graph.py && git commit -m "refactor: chat_graph.py to create_agent + KnowledgeRefs/ReportContext middleware"
```

---

## Task 4: interp_graph.py — agent_batch 换 create_agent + ToolStrategy

**Files:**
- Modify: `backend/app/ai/agents/interp_graph.py`
- Modify: `backend/tests/ai/agents/test_interp_graph.py`

**Interfaces:**
- Produces: `InterpBatchItem`/`InterpBatchResult`（Pydantic）、`InterpAgentState`、`InterpKnowledgeMiddleware`、`build_interp_agent()`、`agent_batch` 节点（调用 create_agent 子图）、`build_interp_graph(hospital_id, db)`、`run_interpretation_agent(...)`（签名不变）
- Deletes: `InterpBatchResult` TypedDict、手写 for 循环 + 正则解析

- [ ] **Step 1: 写失败测试**

替换 `backend/tests/ai/agents/test_interp_graph.py` 全部内容为：

```python
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
    from app.ai.agents.interp_graph import build_interp_graph
    with patch("app.ai.agents.interp_graph.get_chat_model") as mock_model:
        mock_model.return_value = MagicMock()
        graph = build_interp_graph("H001", MagicMock())
        # 取出 agent_batch 节点函数直接调用
        agent_batch_fn = None
        for name, node in graph.nodes.items():
            if name == "agent_batch":
                agent_batch_fn = node.fn if hasattr(node, "fn") else node
                break
        assert agent_batch_fn is not None
        result = agent_batch_fn({
            "abnormal_indicators": [],
            "hospital_id": "H001",
            "report_id": 1,
        })
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
    # refs 按 knowledge_ref_ids 反查
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
    """测试辅助：构造最小图跑 agent_batch"""
    pass
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/agents/test_interp_graph.py -v`
Expected: FAIL — `ImportError: cannot import name 'InterpBatchResult'`（因现有的是 TypedDict 非 BaseModel）或 `_map_structured_to_explanations` 或 `build_interp_agent`

- [ ] **Step 3: 重写 interp_graph.py**

替换 `backend/app/ai/agents/interp_graph.py` 全部内容为：

```python
import json
from datetime import datetime
from typing import List, Optional, TypedDict

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage, SystemMessage, ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.graph import StateGraph, END
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing_extensions import NotRequired

from app.ai.llm import get_chat_model
from app.ai.agents.tools import AgentContext, INTERP_TOOLS
from app.config import settings

INTERP_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，
为体检者撰写易懂的指标解读和健康建议。

规则:
1. 绿区指标一笔带过，重点解读红区和黄区
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统的"注意饮食"
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"

你有以下工具可用：
- search_knowledge: 搜索医学知识库（对每个异常指标都应查询相关知识）
- get_triage_rules: 获取三色分级规则

对每个异常指标生成 explanation（解读）和 suggestion（建议），引用知识库注明来源。"""


class InterpBatchItem(BaseModel):
    """单指标的解读结果"""
    indicator_id: int = Field(description="异常指标 ID")
    explanation: str = Field(description="指标解读文字")
    suggestion: str = Field(description="健康建议文字")
    knowledge_ref_ids: list[int] = Field(default_factory=list, description="解读该指标时引用的 search_knowledge 结果 entry_id 列表")


class InterpBatchResult(BaseModel):
    """本报告所有异常指标的批量解读结果"""
    items: list[InterpBatchItem]


class InterpState(TypedDict):
    hospital_id: str
    report_id: int
    indicators: List[dict]
    judgments: List[dict]
    abnormal_indicators: List[dict]
    agent_explanations: dict
    knowledge_refs: dict
    overall_level: str
    red_count: int
    yellow_count: int
    green_count: int


class InterpAgentState(AgentState):
    knowledge_results: NotRequired[dict]


def _extract_refs_dict_from_tool_result(result) -> dict:
    """从 ToolMessage 或 Command 解析 search_knowledge 返回的 {entry_id: ref}"""
    msgs = []
    if isinstance(result, Command):
        msgs = (result.update or {}).get("messages", [])
    else:
        msgs = [result]
    refs_dict = {}
    for m in msgs:
        if isinstance(m, ToolMessage):
            try:
                data = json.loads(m.content)
                if isinstance(data, list):
                    for r in data:
                        eid = r.get("entry_id")
                        if eid is not None:
                            refs_dict[eid] = {"entry_id": eid, "title": r.get("title")}
            except (json.JSONDecodeError, TypeError):
                pass
    return refs_dict


class InterpKnowledgeMiddleware(AgentMiddleware):
    """拦截 search_knowledge，把 {entry_id→ref} 累积进 state.knowledge_results"""

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        result = handler(request)
        if request.tool_call["name"] == "search_knowledge":
            refs_dict = _extract_refs_dict_from_tool_result(result)
            if refs_dict:
                if isinstance(result, Command):
                    update = dict(result.update or {})
                    update["knowledge_results"] = refs_dict
                    return Command(update=update)
                return Command(update={"knowledge_results": refs_dict})
        return result


def build_interp_agent():
    """构造 interpretation Agent 子图（create_agent + ToolStrategy）"""
    model = get_chat_model(streaming=False)
    return create_agent(
        model=model,
        tools=INTERP_TOOLS,
        system_prompt=INTERP_SYSTEM_PROMPT,
        middleware=[InterpKnowledgeMiddleware()],
        response_format=ToolStrategy(InterpBatchResult),
        state_schema=InterpAgentState,
    )


def _map_structured_to_explanations(
    structured: InterpBatchResult,
    knowledge_results: dict,
    abnormal_indicators: list[dict],
) -> tuple[dict, dict]:
    """把结构化输出映射为 explanations/refs，并补全未出现的异常指标"""
    explanations = {}
    mapped_refs = {}
    for item in structured.items:
        explanations[item.indicator_id] = {
            "explanation": item.explanation,
            "suggestion": item.suggestion,
        }
        ref_ids = set(item.knowledge_ref_ids)
        mapped_refs[item.indicator_id] = [
            knowledge_results.get(rid) for rid in ref_ids
            if knowledge_results.get(rid)
        ] or list(knowledge_results.values())

    all_refs = list(knowledge_results.values())
    for ind in abnormal_indicators:
        iid = ind["indicator_id"]
        if iid not in explanations:
            explanations[iid] = {"explanation": "", "suggestion": ""}
        if iid not in mapped_refs:
            mapped_refs[iid] = all_refs

    return explanations, mapped_refs


def build_interp_graph(hospital_id: str, db: Session):
    """构造 interpretation Agent 的外层 StateGraph"""

    def load_indicators(state: InterpState) -> dict:
        report_id = state["report_id"]
        rows = db.execute(
            text("SELECT id, item_name, item_name_standard, result_value, unit, "
                 "ref_range_low, ref_range_high FROM report_indicator WHERE report_id = :rid ORDER BY id"),
            {"rid": report_id},
        ).fetchall()
        indicators = [
            {"id": r[0], "item_name": r[1], "item_name_standard": r[2],
             "result_value": r[3], "unit": r[4],
             "ref_range_low": r[5], "ref_range_high": r[6]}
            for r in rows
        ]
        return {"indicators": indicators}

    def run_rules(state: InterpState) -> dict:
        from app.modules.interpretation.rules_engine import rules_engine
        from app.modules.interpretation.service import list_rules

        rules = list_rules(db)
        rules_engine.load_rules(state["hospital_id"], [{
            "id": r.id, "rule_name": r.rule_name, "rule_type": r.rule_type,
            "indicator_code": r.indicator_code, "conditions": r.conditions,
            "color_level": r.color_level, "priority": r.priority, "is_active": r.is_active,
        } for r in rules])

        judgments = []
        red_count = yellow_count = green_count = 0
        for ind in state["indicators"]:
            ind_dict = {
                "item_name": ind["item_name"],
                "item_name_standard": ind["item_name_standard"],
                "result_value": ind["result_value"],
                "unit": ind["unit"],
                "ref_range_low": ind["ref_range_low"],
                "ref_range_high": ind["ref_range_high"],
            }
            result = rules_engine.evaluate(state["hospital_id"], ind_dict)

            deviation = result.deviation
            if deviation == "normal":
                try:
                    val = float(ind["result_value"] or 0)
                    ref_high = float(ind["ref_range_high"] or 0)
                    ref_low = float(ind["ref_range_low"] or 0)
                    if ref_high and val > ref_high:
                        deviation = "high"
                    elif ref_low and val < ref_low:
                        deviation = "low"
                except (ValueError, TypeError):
                    pass

            judgments.append({
                "indicator_id": ind["id"],
                "item_name": ind["item_name"],
                "result_value": ind["result_value"],
                "deviation": deviation,
                "color_level": result.color_level,
                "matched_rule_id": result.matched_rule_id,
            })

            if result.color_level == "red":
                red_count += 1
            elif result.color_level == "yellow":
                yellow_count += 1
            else:
                green_count += 1

        overall = "green"
        if red_count > 0:
            overall = "red"
        elif yellow_count > 0:
            overall = "yellow"

        return {
            "judgments": judgments,
            "overall_level": overall,
            "red_count": red_count,
            "yellow_count": yellow_count,
            "green_count": green_count,
        }

    def filter_abnormal(state: InterpState) -> dict:
        abnormal = [
            {**j, **{"item_name_standard": next(
                (i["item_name_standard"] for i in state["indicators"] if i["id"] == j["indicator_id"]),
                None
            ), "unit": next(
                (i["unit"] for i in state["indicators"] if i["id"] == j["indicator_id"]),
                None
            ), "ref_range_low": next(
                (i["ref_range_low"] for i in state["indicators"] if i["id"] == j["indicator_id"]),
                None
            ), "ref_range_high": next(
                (i["ref_range_high"] for i in state["indicators"] if i["id"] == j["indicator_id"]),
                None
            )}}
            for j in state["judgments"]
            if j["color_level"] in ("red", "yellow")
        ]
        return {"abnormal_indicators": abnormal}

    def agent_batch(state: InterpState) -> dict:
        if not state["abnormal_indicators"]:
            return {"agent_explanations": {}, "knowledge_refs": {}}

        agent = build_interp_agent()
        indicator_lines = []
        for ind in state["abnormal_indicators"]:
            ref = f"{ind.get('ref_range_low','-')}-{ind.get('ref_range_high','-')}"
            indicator_lines.append(
                f"[ID:{ind['indicator_id']}] {ind['item_name']}: "
                f"值 {ind['result_value']}{ind.get('unit','')}, "
                f"参考区间 {ref}, {ind['deviation']}, {ind['color_level']}区"
            )
        indicators_text = "\n".join(indicator_lines)

        user_content = f"""以下是本报告的异常指标，请对每个查相关医学知识并生成解读+建议：

{indicators_text}

对每个指标调用 search_knowledge 查询相关知识，然后输出结构化结果，每个指标含：
indicator_id（指标 ID）、explanation（解读文字）、suggestion（建议文字）、knowledge_ref_ids（引用的 search_knowledge 结果 entry_id 列表）。"""

        result = agent.invoke(
            {"messages": [HumanMessage(content=user_content)]},
            config={"recursion_limit": settings.AGENT_MAX_ITERATIONS * 2},
            context=AgentContext(hospital_id=state["hospital_id"], db_session=db),
        )

        structured = result.get("structured_response")
        knowledge_results = result.get("knowledge_results", {})

        if structured is None:
            import logging
            logging.getLogger(__name__).warning(
                "interp_graph agent_batch got no structured_response for report_id=%s",
                state["report_id"],
            )
            structured = InterpBatchResult(items=[])

        explanations, mapped_refs = _map_structured_to_explanations(
            structured, knowledge_results, state["abnormal_indicators"],
        )
        return {"agent_explanations": explanations, "knowledge_refs": mapped_refs}

    def persist(state: InterpState) -> dict:
        from app.modules.interpretation.models import (
            ReportInterpretation, IndicatorJudgment,
        )
        from app.core.rabbitmq import rabbitmq, TaskMessage

        report_id = state["report_id"]

        db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id,
        ).delete()
        db.commit()

        interp = ReportInterpretation(
            report_id=report_id, status="processing",
        )
        db.add(interp)
        db.commit()
        db.refresh(interp)

        for j in state["judgments"]:
            iid = j["indicator_id"]
            exp_data = state.get("agent_explanations", {}).get(iid, {})
            refs = state.get("knowledge_refs", {}).get(iid, [])
            db.add(IndicatorJudgment(
                interpretation_id=interp.id,
                indicator_id=iid,
                item_name=j["item_name"],
                result_value=j["result_value"],
                deviation=j["deviation"],
                color_level=j["color_level"],
                matched_rule_id=j["matched_rule_id"],
                explanation=exp_data.get("explanation", ""),
                suggestion=exp_data.get("suggestion", ""),
                knowledge_refs=refs or None,
            ))

        interp.red_count = state["red_count"]
        interp.yellow_count = state["yellow_count"]
        interp.green_count = state["green_count"]
        interp.overall_level = state["overall_level"]
        interp.status = "completed"
        interp.completed_at = datetime.utcnow()
        db.commit()

        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=state["hospital_id"], priority=0,
            payload={"event": "interpretation_done", "report_id": report_id,
                     "hospital_id": state["hospital_id"]},
        ))
        return {}

    g = StateGraph(InterpState)
    g.add_node("load_indicators", load_indicators)
    g.add_node("run_rules", run_rules)
    g.add_node("filter_abnormal", filter_abnormal)
    g.add_node("agent_batch", agent_batch)
    g.add_node("persist", persist)
    g.set_entry_point("load_indicators")
    g.add_edge("load_indicators", "run_rules")
    g.add_edge("run_rules", "filter_abnormal")
    g.add_edge("filter_abnormal", "agent_batch")
    g.add_edge("agent_batch", "persist")
    g.add_edge("persist", END)
    return g.compile()


def run_interpretation_agent(hospital_id: str, db: Session, report_id: int) -> dict:
    """同步运行 interpretation 图，返回最终状态"""
    from app.modules.report.models import ReportInfo
    from app.modules.interpretation.models import ReportInterpretation

    report = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not report:
        return {}

    existing = db.query(ReportInterpretation).filter(
        ReportInterpretation.report_id == report_id,
        ReportInterpretation.status == "completed",
    ).first()
    if existing:
        return {}

    graph = build_interp_graph(hospital_id, db)
    try:
        final_state = graph.invoke({
            "hospital_id": hospital_id,
            "report_id": report_id,
            "indicators": [],
            "judgments": [],
            "abnormal_indicators": [],
            "agent_explanations": {},
            "knowledge_refs": {},
            "overall_level": "green",
            "red_count": 0,
            "yellow_count": 0,
            "green_count": 0,
        })
        return final_state
    except Exception as e:
        from app.modules.interpretation.models import ReportInterpretation
        interp = db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id,
            ReportInterpretation.status == "processing",
        ).first()
        if interp:
            interp.retry_count += 1
            interp.status = "failed" if interp.retry_count >= 3 else "pending"
            db.commit()
        raise
```

在文件顶部 import 区加 `from typing import TypedDict`，与现有 `from typing import List, Optional` 合并。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_interp_graph.py -v`
Expected: PASS（7 个测试全过）

- [ ] **Step 5: 验证 interp_migration 测试仍通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_interp_migration.py -v`
Expected: PASS（签名不变，mock 不受影响）

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/ai/agents/interp_graph.py tests/ai/agents/test_interp_graph.py && git commit -m "refactor: interp_graph agent_batch to create_agent + ToolStrategy(InterpBatchResult)"
```

---

## Task 5: __init__.py 导出调整 + 全量验证

**Files:**
- Modify: `backend/app/ai/agents/__init__.py`

**Interfaces:**
- Produces: 导出 `run_chat_agent`/`run_interpretation_agent`/`AgentContext`/`build_chat_agent`/`build_interp_agent`
- Deletes: 导出 `make_tools`/`build_chat_graph`/`build_interp_graph`

- [ ] **Step 1: 更新 __init__.py**

替换 `backend/app/ai/agents/__init__.py` 全部内容为：

```python
from app.ai.agents.chat_graph import (
    run_chat_agent, build_chat_agent, ChatAgentState,
    KnowledgeRefsMiddleware, ReportContextMiddleware,
)
from app.ai.agents.interp_graph import (
    run_interpretation_agent, build_interp_agent, build_interp_graph,
    InterpBatchResult, InterpBatchItem, InterpKnowledgeMiddleware,
)
from app.ai.agents.tools import AgentContext, CHAT_TOOLS, INTERP_TOOLS
```

- [ ] **Step 2: 验证导入无错误**

Run: `cd backend && uv run python -c "from app.ai.agents import run_chat_agent, run_interpretation_agent, AgentContext; print('imports ok')"`
Expected: 输出 `imports ok`

- [ ] **Step 3: 验证 langchain 1.0 API 导入**

Run:
```bash
cd backend && uv run python -c "from langchain.agents import create_agent; from langchain.agents.middleware import AgentMiddleware; from langchain.agents.structured_output import ToolStrategy; print('langchain 1.0 api ok')"
```
Expected: 输出 `langchain 1.0 api ok`

- [ ] **Step 4: 全量 agent 测试**

Run: `cd backend && uv run pytest tests/ai/agents/ -v`
Expected: PASS（所有 agent 测试通过）

- [ ] **Step 5: 全量 ai 测试回归**

Run: `cd backend && uv run pytest tests/ai/ -v`
Expected: PASS（rag/llm/config 测试不受影响）

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/ai/agents/__init__.py && git commit -m "refactor: update ai/agents exports for create_agent API"
```

---

## Self-Review

**Spec coverage check:**
- §1 依赖升级 → Task 1 ✓
- §2 tools.py AgentContext + ToolRuntime → Task 2 ✓
- §3 chat_graph create_agent + 中间件 → Task 3 ✓
- §4 interp_graph agent_batch + ToolStrategy → Task 4 ✓
- §5 测试改造 → Tasks 2-4 内嵌 + Task 5 全量验证 ✓
- §6 __init__.py 导出 → Task 5 ✓

**Placeholder scan:** Task 4 Step 3 有一个 `InterpState` 占位写法的明确修正说明（不是遗漏，是编辑指引）。无其他 TBD/TODO。

**Type consistency check:**
- `AgentContext` 在 Task 2 定义，Task 3/4 使用（字段 `hospital_id`/`db_session` 一致）✓
- `CHAT_TOOLS`/`INTERP_TOOLS` 在 Task 2 定义，Task 3/4 使用 ✓
- `InterpBatchResult`/`InterpBatchItem` 在 Task 4 定义，测试用同一名称 ✓
- `_map_structured_to_explanations` 在 Task 4 定义，测试用同一名称 ✓
- `KnowledgeRefsMiddleware`/`ReportContextMiddleware`/`InterpKnowledgeMiddleware` 命名一致 ✓
- `build_chat_agent(report_id)` / `build_interp_agent()` 签名一致 ✓
