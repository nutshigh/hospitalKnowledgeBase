import json
import logging
from typing import AsyncIterator, Optional

from langchain.agents import AgentState, create_agent
from sqlalchemy.orm import Session
from langchain.agents.middleware import AgentMiddleware
from langchain.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import BaseModel, Field
from typing_extensions import Annotated, NotRequired

from app.ai.llm import get_chat_model
from app.ai.agents.tools import AgentContext, CHAT_TOOLS
from app.ai.agents.think_filter import ThinkStreamFilter, strip_think_tags
from app.ai.agents.citation_matcher import inject_citations
from app.config import settings

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，为体检者提供易懂的健康咨询。

## 核心规则
1. 必须先调用 search_knowledge 工具搜索知识库，获取相关医学知识后再回答。禁止不搜知识库直接回答。
2. 基于知识库和报告数据回答，不编造信息
3. 建议具体可执行，避免笼统
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"
6. 用户未关联报告时，引导其先上传报告以获取更精准建议

## 工具使用要求
回答任何健康、疾病、指标、医学相关问题时，你必须先调用 search_knowledge 工具。
即使用户没有明确要求搜索知识库，你也要主动搜索以获取准确信息。
只有纯闲聊（如"你好""谢谢"）可以不调用工具。

示例：
用户："高血压有什么并发症？" → 你必须先调用 search_knowledge("高血压 并发症")，再基于检索结果回答
用户："我的血糖正常吗？" → 你必须先调用 get_report_indicators() 获取本报告指标，再调用 search_knowledge("血糖 参考范围") 查询知识
用户："你好" → 可以直接回答，不需要工具

## 确定性分级
- 基于指标数值与参考范围直接对比的结论，用确定的语气陈述
- 基于知识库推理但非直接数值判断的结论，用"可能""建议进一步检查"等不确定语气
- 信息不足或超出能力范围时，明确告知无法判断，不做猜测

## 可用工具
- search_knowledge: 搜索医学知识库（回答健康/疾病问题时必须调用）
- get_report_indicators: 获取当前会话关联报告的指标数据（无需传参）
- get_report_summary: 获取当前会话关联报告的概览（无需传参）
- get_user_history_reports: 获取当前用户的历年报告概览（无需传参）
- get_indicator_history: 获取某指标的历史趋势（仅需传 item_name）
- get_triage_rules: 获取三色分级规则"""


def _accumulate_refs(existing: list, new: list) -> list:
    return existing + new


class ChatAgentState(AgentState):
    knowledge_refs: NotRequired[Annotated[list[dict], _accumulate_refs]]


def _extract_refs_from_tool_result(result) -> list[dict]:
    """从 ToolMessage 或 Command 解析 search_knowledge 返回的 refs（文档+KG 结果）"""
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
                    for r in data:
                        refs.append({
                            "entry_id": r.get("entry_id"),
                            "title": r.get("title", ""),
                            "source": r.get("source", "document"),
                        })
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
                return Command(update={"knowledge_refs": refs, "messages": [result]})
        return result

    async def awrap_tool_call(self, request: ToolCallRequest, handler):
        result = await handler(request)
        if request.tool_call["name"] == "search_knowledge":
            refs = _extract_refs_from_tool_result(result)
            if refs:
                if isinstance(result, Command):
                    update = dict(result.update or {})
                    update["knowledge_refs"] = refs
                    return Command(update=update)
                return Command(update={"knowledge_refs": refs, "messages": [result]})
        return result


class ReportContextMiddleware(AgentMiddleware):
    """在 system_message 末尾追加 report_id 提示语，仅作语义增强。
    工具入参的 report_id 由 AgentContext 注入，不依赖模型自行填写。
    """

    def __init__(self, report_id: Optional[int]):
        super().__init__()
        self.report_id = report_id

    def _augment(self, request):
        if not self.report_id or request.system_message is None:
            return request
        extra_text = f"\n\n当前会话关联的报告 ID 是 {self.report_id}。用户提问关于本报告的指标时，可直接调用 get_report_indicators（无需传 report_id 参数）。"
        new_content = list(request.system_message.content_blocks) + [{"type": "text", "text": extra_text}]
        new_sys = SystemMessage(content=new_content)
        return request.override(system_message=new_sys)

    def wrap_model_call(self, request, handler):
        return handler(self._augment(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._augment(request))


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


class ChatStructuredResult(BaseModel):
    """聊天回复的结构化元数据（流式文本后的尾随事件）"""
    certainty: str = Field(description="确定性: definite | probable | refused")
    certainty_reason: str = Field(default="", description="确定性判定理由")
    citations: list[dict] = Field(default_factory=list, description="引用列表 [{ref_id, entry_id, title, source, content}]")


def _classify_certainty(text: str) -> tuple[str, str]:
    """轻量确定性分类（基于关键词规则，无需额外 LLM 调用）。"""
    refused_keywords = ["无法判断", "信息不足", "需要进一步检查", "无法确定", "建议咨询医生", "超出能力"]
    probable_keywords = ["可能", "建议进一步", "推测", "疑似", "或许", "可能存在", "不排除"]

    for kw in refused_keywords:
        if kw in text:
            return "refused", f"回复包含不确定表述: '{kw}'"
    for kw in probable_keywords:
        if kw in text:
            return "probable", f"回复包含推测性表述: '{kw}'"
    return "definite", "基于明确指标数值或知识库直接陈述"


def _build_sources_from_refs(refs: list[dict]) -> list[dict]:
    """把 knowledge_refs 转为 citation_matcher 需要的 sources 格式。

    refs: [{entry_id, title, source}]（无 content）
    对于没有 content 的 ref，用 title 作为匹配文本。
    """
    sources = []
    for r in refs:
        sources.append({
            "entry_id": r.get("entry_id"),
            "title": r.get("title", ""),
            "source": r.get("source", "document"),
            "content": r.get("title", ""),  # refs 中无 content，用 title 代替
        })
    return sources


async def _extract_structured_metadata(response_text: str, refs: list[dict]) -> dict:
    """提取聊天的结构化元数据：certainty + 后置注入 citations。"""
    try:
        certainty, certainty_reason = _classify_certainty(response_text)
        # 后置 citation 注入：embedding 相似度匹配
        sources = _build_sources_from_refs(refs)
        annotated_text, citations = inject_citations(response_text, sources)
        return {
            "certainty": certainty,
            "certainty_reason": certainty_reason,
            "citations": citations,
            "annotated_text": annotated_text,
        }
    except Exception as e:
        logger.warning("structured metadata extraction failed: %s", e)
        return {"certainty": "probable", "certainty_reason": "", "citations": [], "annotated_text": response_text}


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

        agent = build_chat_agent(session.report_id)
        ctx = AgentContext(hospital_id=hospital_id, report_id=session.report_id, user_id=user_id)
        inputs = {"messages": history_msgs + [HumanMessage(content=user_message)]}
        config = {"recursion_limit": settings.AGENT_MAX_ITERATIONS * 2}

        final_response = ""
        final_state = None
        think_filter = ThinkStreamFilter()
        async for event in agent.astream_events(inputs, version="v2", config=config, context=ctx):
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
                        clean = think_filter.feed(chunk.content)
                        if clean:
                            yield {"event": "token", "data": {"content": clean}}
            elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                final_state = event.get("data", {}).get("output")

        # 流结束：冲刷 thinking 过滤器缓冲区
        tail = think_filter.flush()
        if tail:
            yield {"event": "token", "data": {"content": tail}}

        # Fallback: 某些模型在 agent 工具调用流程下不产生 on_chat_model_stream
        # token，最终回复只在 final_state 的最后一条 AIMessage 里。此时把完整
        # 内容作为单个 token 事件补发，避免空回复。
        if not final_response and final_state:
            msgs = (final_state or {}).get("messages", [])
            for m in reversed(msgs):
                if isinstance(m, AIMessage) and m.content:
                    final_response = strip_think_tags(m.content)
                    yield {"event": "token", "data": {"content": final_response}}
                    break

        # 入库前统一清洗 thinking 标签
        final_response = strip_think_tags(final_response)

        refs = (final_state or {}).get("knowledge_refs", [])

        # 后置 citation 注入 + 确定性分类
        structured_data = await _extract_structured_metadata(final_response, refs)
        annotated_text = structured_data.get("annotated_text", final_response)
        citations = structured_data.get("citations", [])

        # 用标注后的文本和 citations 入库
        msg = chat_service.save_message(
            db, session_id, "assistant", annotated_text, knowledge_refs=citations or None
        )

        if not session.title:
            title = user_message[:50] + ("..." if len(user_message) > 50 else "")
            db.query(type(session)).filter(type(session).id == session_id).update({"title": title})
            db.commit()

        # 尾随 structured 事件（前端据此渲染确定性标注和引用列表）
        yield {"event": "structured", "data": structured_data}
        yield {"event": "done", "data": {"message_id": msg.id}}
    except Exception as e:
        yield {"event": "error", "data": {"message": f"AI 响应失败: {e}"}}
    finally:
        _session_locks.discard(session_id)
