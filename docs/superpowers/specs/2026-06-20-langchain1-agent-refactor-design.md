# LangChain 1.0 Agent 改造设计（create_agent + 中间件）

> 日期：2026-06-20
> 状态：设计已确认，待写实现计划
> 范围：将 `ai/agents/` 的手写工具循环/消息管理改造为 LangChain 1.0 的 `create_agent` + 中间件范式。RAG 层、modules 层调用契约、SSE 事件、MySQL 表结构不变。
> 前置 spec：`2026-06-18-llamaindex-langchain-integration-design.md`

---

## 背景与动机

`2026-06-18-llamaindex-langchain-integration-design.md` 已完成 LlamaIndex RAG + LangGraph Agent 集成，但 Agent 部分（`ai/agents/chat_graph.py`、`interp_graph.py`、`tools.py`）写成了手写工具循环 + 手动消息管理，不符合 LangChain 1.0 的 `create_agent` + 中间件范式。

**现状问题：**

- `chat_graph.py:54-95` 手写 `agent_node` + `tool_node` + `should_continue` 三件套，手动 `bind_tools`、手动解析 `tool_calls`、手动构造 `ToolMessage`
- `interp_graph.py:158-254` `agent_batch` 里手写 `for i in range(max_iter)` 工具调用循环 + 正则 `re.search(r'\[.*\]')` 解析 JSON（脆弱，依赖 LLM 输出格式）
- `tools.py:10-117` 用闭包 `make_tools(hospital_id, db_session)` 绑定依赖（非 LangChain 1.0 惯例，工具不可独立复用、测试需 mock 闭包）

**目标：** 用 `create_agent` 替代手写工具循环，用 `context_schema` + `ToolRuntime` 替代闭包绑定，用 `response_format=ToolStrategy` 替代正则解析，用 `wrap_tool_call` 中间件捕获 knowledge_refs。完全替换上述手写实现。

---

## 关键决策汇总

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | 改造范围 | 仅 `ai/agents/` 三文件 + 依赖版本 + 受影响测试。RAG/modules/SSE/表结构不动 |
| 2 | 依赖注入方式 | `context_schema=AgentContext` + `ToolRuntime`（工具从 `runtime.context` 取 hospital_id/db） |
| 3 | interp_graph 结构 | 保留外层 StateGraph（确定性节点），`agent_batch` 换成 `create_agent` 子图 |
| 4 | interp 结构化输出 | `response_format=ToolStrategy(InterpBatchResult)` 自动校验+重试，删正则 |
| 5 | knowledge_refs 捕获 | `wrap_tool_call` 中间件 + `Command` 注入 state（工具保持纯函数） |
| 6 | chat 动态 system prompt | `wrap_model_call` 中间件（`ReportContextMiddleware`）追加 report_id 到 system_message |
| 7 | streaming 版本 | `stream_events(version="v3")`（1.0 推荐） |
| 8 | 并发控制/memory | 保留 `_session_locks` + MySQL history（决策 #12 不变） |

---

## §1 总览与依赖升级

### 1.1 改造范围

| 文件 | 改动 |
|------|------|
| `backend/pyproject.toml` | langchain 依赖主版本升级 |
| `backend/app/ai/agents/tools.py` | 闭包 → 模块级 `@tool` + `ToolRuntime` |
| `backend/app/ai/agents/chat_graph.py` | 手写 StateGraph → `create_agent` + 中间件 |
| `backend/app/ai/agents/interp_graph.py` | `agent_batch` 节点换成 `create_agent` 子图 + `ToolStrategy` |
| `backend/app/ai/agents/__init__.py` | 删 `make_tools` 导出 |
| `backend/tests/ai/agents/*.py` | 适配新 API |

**不动的文件：** `ai/rag/*`、`ai/llm.py`、`ai/config.py`、`modules/chat/*`、`modules/interpretation/*`、`modules/knowledge/*`、`app/config.py`、所有 models。

### 1.2 依赖升级（`pyproject.toml`）

```toml
# 旧
"langchain-core>=0.3,<0.4",
"langchain-openai>=0.2,<0.3",
"langgraph>=0.2,<0.3",
# 新
"langchain>=1.0,<2.0",
"langchain-core>=1.4.7,<2.0",
"langchain-openai>=1.0,<2.0",
"langgraph>=1.2.5,<1.3",
```

- `langchain` 是新依赖（提供 `create_agent`、`AgentState`、`AgentMiddleware`、`ToolStrategy`、`ToolRuntime`）
- 已验证 `llama-index-core`（latest 0.14.x）不依赖 langchain，零冲突
- `ai/llm.py` 的 `get_chat_model` 返回 `ChatOpenAI` 实例，直接传给 `create_agent(model=...)`（不用 `"provider:model"` 字符串，因为要用自定义 base_url 指向 vLLM/远端）

### 1.3 核心映射（旧 → 新）

| 旧手写 | 新 LangChain 1.0 |
|--------|-----------------|
| `StateGraph` + `agent_node`/`tool_node`/`should_continue` | `create_agent(model, tools, system_prompt, middleware)` |
| `model.bind_tools(tools)` | `create_agent(tools=...)` 内部处理 |
| 手写 `for i in range(max_iter)` 工具循环 | create_agent 内置循环 + `recursion_limit` |
| 手写 `tool_node` 解析 `tool_calls` + 构造 `ToolMessage` | create_agent 内置 ToolNode |
| `make_tools(hospital_id, db)` 闭包绑定 | `context_schema=AgentContext` + `ToolRuntime` |
| 正则 `re.search(r'\[.*\]')` 解析 JSON | `response_format=ToolStrategy(InterpBatchResult)` |
| `astream_events(version="v2")` | `stream_events(version="v3")` |
| 手写 tool_node 累积 knowledge_refs | `wrap_tool_call` 中间件 + `Command` |

### 1.4 不变项

- 会话 memory 仍 MySQL（决策 #12）
- `_session_locks` 并发控制保留
- interp 的 `load_indicators`/`rules_engine`/`filter_abnormal`/`persist` 确定性节点保留
- SSE 事件类型（`tool_status`/`token`/`done`/`error`）保留
- `AGENT_MAX_ITERATIONS` 配置保留（映射到 `recursion_limit = AGENT_MAX_ITERATIONS * 2`，每轮含 model+tool 两个 step）
- `CHAT_SYSTEM_PROMPT`/`INTERP_SYSTEM_PROMPT` 文本不变
- 对外 API `run_chat_agent`/`run_interpretation_agent` 签名不变

---

## §2 `tools.py`：ToolRuntime + context_schema

### 2.1 AgentContext

新增 `AgentContext` dataclass（`tools.py` 顶部），调用方通过 `agent.invoke(..., context=AgentContext(hospital_id, db))` 传入：

```python
from dataclasses import dataclass
from sqlalchemy.orm import Session

@dataclass
class AgentContext:
    hospital_id: str
    db_session: Session
```

### 2.2 工具改造

工具从模块级闭包函数改为模块级 `@tool`，依赖从 `runtime.context` 取（`runtime: ToolRuntime[AgentContext]` 参数被框架自动注入，对 LLM 隐藏，不进 tool schema）：

```python
from langchain.tools import tool, ToolRuntime

@tool
def search_knowledge(
    query: str,
    category_ids: Optional[List[int]] = None,
    top_k: Optional[int] = None,
    runtime: ToolRuntime[AgentContext],
) -> list[dict]:
    """搜索医学知识库，返回相关知识条目。...（docstring 不变）"""
    ctx = runtime.context
    results = ai_rag.search(ctx.hospital_id, query, category_ids=category_ids, top_k=top_k)
    return [{"entry_id": r.entry_id, "title": r.title, "content": r.content, "score": r.score} for r in results]
```

- 其余 5 个工具（`get_report_indicators`/`get_report_summary`/`get_user_history_reports`/`get_indicator_history`/`get_triage_rules`）同样改造，从 `runtime.context.db_session` 取 db
- 工具签名/docstring 内容不变，只加 `runtime: ToolRuntime[AgentContext]` 参数
- `make_tools()` 函数删除
- 工具列表导出为模块级常量 `CHAT_TOOLS = [search_knowledge, ...]` 和 `INTERP_TOOLS = [search_knowledge, get_triage_rules]`（chat 用全部 6 个，interp 只用 2 个）

### 2.3 影响

- `ai/agents/__init__.py` 删 `make_tools` 导出，改为导出 `AgentContext`、`CHAT_TOOLS`、`INTERP_TOOLS`
- `chat_graph.py:50`/`interp_graph.py:162` 的 `tools = make_tools(hospital_id, db)` 调用删除
- `test_tools.py` 改造：不再调 `make_tools("H001", db)`，改为直接 invoke 工具时传 mock context

---

## §3 `chat_graph.py`：create_agent + 中间件

### 3.1 State 定义

```python
from langchain.agents import AgentState
from typing_extensions import Annotated, NotRequired

def _accumulate_refs(existing: list, new: list) -> list:
    return existing + new

class ChatAgentState(AgentState):
    knowledge_refs: NotRequired[Annotated[list[dict], _accumulate_refs]]
```

- `AgentState` 自带 `messages`（带 `add_messages` reducer）
- 删除原 `ChatState` 的 `hospital_id`/`session_id`/`user_id`/`report_id`/`final_response`——由 `run_chat_agent` 在外层管理（hospital_id 走 context，其余走闭包/局部变量）

### 3.2 KnowledgeRefsMiddleware（`wrap_tool_call`）

拦截 `search_knowledge`，把 `{entry_id, title}` 累积进 `state.knowledge_refs`：

```python
from langchain.agents.middleware import AgentMiddleware
from langchain.tools.tool_node import ToolCallRequest
from langchain.messages import ToolMessage
from langgraph.types import Command
import json

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


def _extract_refs_from_tool_result(result) -> list[dict]:
    """从 ToolMessage 或 Command 的 messages 里解析 search_knowledge 返回的 JSON list"""
    if isinstance(result, Command):
        msgs = result.update.get("messages", []) if hasattr(result, "update") else []
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
```

- 放 `chat_graph.py`（chat 专用）
- 工具保持纯函数，捕获逻辑集中一处

### 3.3 ReportContextMiddleware（`wrap_model_call`）

替代原 `chat_graph.py:55-57` 手拼 system prompt 加 report_id：

```python
from langchain.messages import SystemMessage

class ReportContextMiddleware(AgentMiddleware):
    """把 report_id 上下文追加到 system_message"""

    def __init__(self, report_id: int | None):
        super().__init__()
        self.report_id = report_id

    def wrap_model_call(self, request, handler):
        if self.report_id:
            extra_text = f"\n\n当前会话关联的报告 ID 是 {self.report_id}，用户提问时可用 get_report_indicators 获取详细指标。"
            new_content = list(request.system_message.content_blocks) + [{"type": "text", "text": extra_text}]
            new_sys = SystemMessage(content=new_content)
            return handler(request.override(system_message=new_sys))
        return handler(request)
```

- 每次调用 `run_chat_agent` 时按 `session.report_id` 实例化，传给 `create_agent(middleware=[...])`
- 用 `content_blocks` 操作（1.0 文档推荐，兼容 string/list content）

### 3.4 build_chat_agent

```python
from langchain.agents import create_agent

def build_chat_agent(report_id: int | None):
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
```

- 不再接收 `hospital_id`/`db`（走 context）
- 不再 `bind_tools`（create_agent 处理）
- 每请求新建（middleware 带状态）

### 3.5 run_chat_agent 改造

保留外层骨架（`_session_locks`、save_message、history 加载、SSE yield），中间换成 `build_chat_agent` + `stream_events`：

- `build_chat_agent(session.report_id)`
- `agent.stream_events({"messages": history_msgs + [HumanMessage(user_message)]}, version="v3", context=AgentContext(hospital_id, db))`
- SSE 事件映射保持：`on_tool_start`/`on_tool_end` → `tool_status`；`on_chat_model_stream` 且 chunk 无 `tool_call_chunks` → `token`；结束 → `done`；异常 → `error`
- `recursion_limit = settings.AGENT_MAX_ITERATIONS * 2`（通过 `config={"recursion_limit": ...}` 传入）
- 跑完从最终 state snapshot 取 `knowledge_refs` 落库（通过 `stream_events` 的 snapshot 或 `agent.invoke`）
- `_session_locks` 保留，title 自动生成逻辑保留

**v3 streaming 注意：** `stream_events(version="v3")` 事件结构可能与 v2 略有差异，实现时以 1.0 文档为准。state snapshot 通过 stream 的 `values` 取（1.0 推荐 `stream_events` + `values`）。

---

## §4 `interp_graph.py`：外层 StateGraph + agent_batch 子图

### 4.1 保留的外层结构

```
load_indicators → run_rules → filter_abnormal → agent_batch → persist → END
```

`load_indicators`/`run_rules`/`filter_abnormal`/`persist` 四个确定性节点**原样保留**。`InterpState` TypedDict 字段不变（`indicators`/`judgments`/`abnormal_indicators`/`agent_explanations`/`knowledge_refs`/`overall_level`/`red_count`/`yellow_count`/`green_count`）。`build_interp_graph` 仍是外层 StateGraph 串联 5 节点。

### 4.2 InterpBatchResult 改为 Pydantic 模型

`ToolStrategy` 的 schema 必须是 Pydantic/dataclass/TypedDict 对象包裹（非裸数组）：

```python
from pydantic import BaseModel, Field

class InterpBatchItem(BaseModel):
    """单指标的解读结果"""
    indicator_id: int = Field(description="异常指标 ID")
    explanation: str = Field(description="指标解读文字")
    suggestion: str = Field(description="健康建议文字")
    knowledge_ref_ids: list[int] = Field(default_factory=list, description="解读该指标时引用的 search_knowledge 结果 entry_id 列表")

class InterpBatchResult(BaseModel):
    """本报告所有异常指标的批量解读结果"""
    items: list[InterpBatchItem]
```

删除原 `TypedDict InterpBatchResult`（line 32-36）。

### 4.3 InterpKnowledgeMiddleware

interp 的 knowledge_refs 中间件，把 `{entry_id → {entry_id, title}}` 存进 `state.knowledge_results`（不在此做 per-indicator 归属，那是 LLM 通过结构化输出 `knowledge_ref_ids` 声明的）：

```python
class InterpAgentState(AgentState):
    knowledge_results: NotRequired[dict]  # entry_id → {entry_id, title}

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
```

`knowledge_results` 用 last-wins reducer（dict merge：新 dict 覆盖旧 dict 的同 key）或框架默认 reducer。

### 4.4 build_interp_agent

```python
from langchain.agents.structured_output import ToolStrategy

def build_interp_agent():
    model = get_chat_model(streaming=False)
    return create_agent(
        model=model,
        tools=INTERP_TOOLS,
        system_prompt=INTERP_SYSTEM_PROMPT,
        middleware=[InterpKnowledgeMiddleware()],
        response_format=ToolStrategy(InterpBatchResult),
        state_schema=InterpAgentState,
    )
```

- `ToolStrategy` 而非 `ProviderStrategy`——vLLM/OpenAI 兼容端点的 native structured output 支持不确定，ToolStrategy 走 tool-calling 通道，兼容性更好
- `handle_errors=True`（默认）——schema 校验失败时框架自动让 LLM 重试，替代手写 fallback

### 4.5 agent_batch 节点改造

作为外层 StateGraph 的节点，内部调用 create_agent 子图：

```python
def agent_batch(state: InterpState) -> dict:
    if not state["abnormal_indicators"]:
        return {"agent_explanations": {}, "knowledge_refs": {}}

    agent = build_interp_agent()
    indicator_lines = [...]  # 同原逻辑，构造异常指标文本
    user_content = f"以下是本报告的异常指标...对每个指标调用 search_knowledge...输出 JSON..."

    result = agent.invoke(
        {"messages": [HumanMessage(content=user_content)]},
        config={"recursion_limit": settings.AGENT_MAX_ITERATIONS * 2},
        context=AgentContext(state["hospital_id"], db),
    )

    structured = result["structured_response"]  # InterpBatchResult 实例
    knowledge_results = result.get("knowledge_results", {})  # 中间件累积

    # 按 knowledge_ref_ids 反查映射
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
        ] or list(knowledge_results.values())  # fallback: 若 LLM 未声明 ref_ids，用全部

    # 补全未在结构化输出中出现的异常指标（同原逻辑 line 247-252）
    for ind in state["abnormal_indicators"]:
        iid = ind["indicator_id"]
        if iid not in explanations:
            explanations[iid] = {"explanation": "", "suggestion": ""}
        if iid not in mapped_refs:
            mapped_refs[iid] = list(knowledge_results.values())

    return {"agent_explanations": explanations, "knowledge_refs": mapped_refs}
```

删除原 line 162-254 的手写循环、`tools_by_name`、`messages.append`、正则解析、`iterations_used` 警告。`recursion_limit` 替代 `max_iter` 循环。

### 4.6 不变项

- `load_indicators`/`run_rules`/`filter_abnormal`/`persist` 节点逻辑不变
- `run_interpretation_agent` 函数签名和错误处理不变
- `INTERP_SYSTEM_PROMPT` 文本不变
- 失败处理（`ReportInterpretation.status="failed"`，retry_count++）不变

---

## §5 测试改造与验证

### 5.1 受影响测试

| 文件 | 改动 |
|------|------|
| `test_tools.py` | `make_tools("H001", db)` → 直接 invoke 工具传 mock context。`assert len(tools)==6` → 断言模块级工具集合 |
| `test_chat_graph.py` | `patch make_tools` + `patch get_chat_model` → `patch create_agent`（或保留 mock model，断言 `build_chat_agent` 返回非 None）；`ChatState` → `ChatAgentState` |
| `test_interp_graph.py` | 同上；`InterpState` 字段断言不变；`patch make_tools` 删除 |
| `test_chat_migration.py` | 不变（mock `run_chat_agent`，签名不变） |
| `test_interp_migration.py` | 不变（mock `run_interpretation_agent`，签名不变） |

### 5.2 新增测试

- `test_tools.py`：`test_search_knowledge_uses_context`——invoke 工具传 `AgentContext(hospital_id="H002", ...)`，断言 `ai_rag.search` 第一参数是 `"H002"`（验证 context 注入）
- `test_chat_graph.py`：`test_knowledge_refs_middleware_accumulates`——mock handler 返回含 search_knowledge 结果的 ToolMessage，断言中间件返回的 `Command.update["knowledge_refs"]` 含正确 refs
- `test_interp_graph.py`：`test_agent_batch_uses_structured_response`——mock `create_agent` 返回 `{"structured_response": InterpBatchResult(items=[...]), "knowledge_results": {...}}`，断言 `agent_batch` 正确映射 per-indicator refs

### 5.3 验证命令

```bash
cd backend && uv run pytest tests/ai/agents/ -v
cd backend && uv run pytest tests/ai/ -v
cd backend && uv run python -c "from app.ai.agents import run_chat_agent, run_interpretation_agent; print('imports ok')"
cd backend && uv run python -c "from langchain.agents import create_agent; from langchain.agents.middleware import AgentMiddleware; from langchain.agents.structured_output import ToolStrategy; print('langchain 1.0 api ok')"
```

### 5.4 不做的测试

不加集成测试（需 vLLM/Milvus 真实环境），保持现有 mock 单测覆盖。不引入 checkpointer 测试（决策 #12）。

---

## §6 迁移影响与边界

### 6.1 受影响文件清单

| 文件 | 改动程度 | 说明 |
|------|---------|------|
| `backend/pyproject.toml` | 改 | langchain 依赖主版本升级 |
| `backend/app/ai/agents/tools.py` | 重写 | 闭包 → 模块级 `@tool` + `ToolRuntime` |
| `backend/app/ai/agents/chat_graph.py` | 重写 | 手写 StateGraph → `create_agent` + 中间件 |
| `backend/app/ai/agents/interp_graph.py` | 改 | `agent_batch` 节点换成 `create_agent` 子图 + `ToolStrategy`，外层不变 |
| `backend/app/ai/agents/__init__.py` | 改 | 导出调整 |
| `backend/tests/ai/agents/test_tools.py` | 改 | 适配 ToolRuntime |
| `backend/tests/ai/agents/test_chat_graph.py` | 改 | 适配 create_agent |
| `backend/tests/ai/agents/test_interp_graph.py` | 改 | 适配 create_agent |
| `backend/tests/ai/agents/test_chat_migration.py` | 不变 | mock 签名不变 |
| `backend/tests/ai/agents/test_interp_migration.py` | 不变 | mock 签名不变 |

### 6.2 对外契约不变

- `run_chat_agent(hospital_id, db, session, user_message, user_id) -> AsyncIterator[dict]` 签名不变
- `run_interpretation_agent(hospital_id, db, report_id) -> dict` 签名不变
- SSE 事件类型不变
- HTTP 接口不变
- MySQL 表结构不变

### 6.3 不做范围外的事（YAGNI）

- 不引入 LangGraph checkpointer（决策 #12）
- 不改 RAG 层（`ai/rag/*`）
- 不改 `ai/llm.py`（`get_chat_model` 返回类型仍 `ChatOpenAI`，兼容 1.0）
- 不改 modules 层调用
- 不引入 `deepagents` / `SummarizationMiddleware` / `HumanInTheLoopMiddleware` 等高级中间件
- 不改 system prompt 文本
- 不改 `AGENT_MAX_ITERATIONS` 配置值

---

## 附：关键文件引用

- 现状 chat 手写图：`backend/app/ai/agents/chat_graph.py:48`（`build_chat_graph`）、`:54`（`agent_node`）、`:62`（`tool_node`）
- 现状 interp 手写循环：`backend/app/ai/agents/interp_graph.py:158`（`agent_batch`）、`:192`（`for i in range(max_iter)`）、`:229`（正则解析）
- 现状闭包工具：`backend/app/ai/agents/tools.py:10`（`make_tools`）
- 现状依赖：`backend/pyproject.toml:27`（langchain-core 0.3）
- 前置 spec：`docs/superpowers/specs/2026-06-18-llamaindex-langchain-integration-design.md`
