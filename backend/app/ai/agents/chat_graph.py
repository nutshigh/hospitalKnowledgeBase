from typing import TypedDict, Annotated, Optional, AsyncIterator, List
import json

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from sqlalchemy.orm import Session

from app.ai.llm import get_chat_model
from app.ai.agents.tools import CHAT_TOOLS
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


class ChatState(TypedDict):
    hospital_id: str
    session_id: int
    user_id: int
    report_id: Optional[int]
    messages: Annotated[list, add_messages]
    knowledge_refs: Annotated[list[dict], _accumulate_refs]
    final_response: str


def build_chat_graph(hospital_id: str, db: Session):
    """构造 chat Agent 的 LangGraph StateGraph"""
    tools = CHAT_TOOLS
    model = get_chat_model(streaming=True).bind_tools(tools)
    tools_by_name = {t.name: t for t in tools}

    def agent_node(state: ChatState):
        sys_content = CHAT_SYSTEM_PROMPT
        if state.get("report_id"):
            sys_content += f"\n\n当前会话关联的报告 ID 是 {state['report_id']}，用户提问时可用 get_report_indicators 获取详细指标。"
        msgs = [SystemMessage(content=sys_content)] + state["messages"]
        resp = model.invoke(msgs)
        return {"messages": [resp]}

    def tool_node(state: ChatState):
        last_msg = state["messages"][-1]
        new_messages = []
        refs = []
        for call in last_msg.tool_calls:
            tool = tools_by_name.get(call["name"])
            if not tool:
                continue
            result = tool.invoke(call["args"])
            if call["name"] == "search_knowledge" and isinstance(result, list):
                refs.extend([
                    {"entry_id": r.get("entry_id"), "title": r.get("title")}
                    for r in result
                ])
            new_messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False),
                "tool_call_id": call["id"],
            })
        return {"messages": new_messages, "knowledge_refs": refs}

    def should_continue(state: ChatState) -> str:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    g = StateGraph(ChatState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tool_node)
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", should_continue)
    g.add_edge("tools", "agent")
    return g.compile()


MAX_HISTORY_ROUNDS = 20

_session_locks: set[int] = set()


async def run_chat_agent(
    hospital_id: str,
    db: Session,
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

        graph = build_chat_graph(hospital_id, db)
        initial_state = {
            "hospital_id": hospital_id,
            "session_id": session_id,
            "user_id": user_id,
            "report_id": session.report_id,
            "messages": history_msgs + [HumanMessage(content=user_message)],
            "knowledge_refs": [],
            "final_response": "",
        }

        final_response = ""
        final_state = None
        async for event in graph.astream_events(initial_state, version="v2"):
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
