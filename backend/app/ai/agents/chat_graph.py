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
    """拦截 search_knowledge，把 {entry_id,title} 累积到 state.knowledge_refs"""

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
    """构造 chat Agent（create_agent + 中间件）。每请求新建，middleware 带状态。"""
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
