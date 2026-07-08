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
from app.ai.agents.chat_planner import run_planner, execute_plan
from app.config import settings

logger = logging.getLogger(__name__)

CHAT_ANSWER_SYSTEM_PROMPT = """你是体检报告解读医生助手。基于下方检索结果和报告数据，为体检者提供易懂的健康咨询。

## 回答规则
1. 优先基于检索到的医学知识回答，不编造数值。若未提供检索结果，基于常识简要回答并提示信息有限。
2. 建议具体可执行，避免笼统的"注意饮食"。
3. 不下疾病诊断，只做健康风险提示。红区指标提示"建议立即就医复查"。
4. 基于指标数值与参考范围直接对比的结论用确定语气；基于知识库推理用"可能""建议进一步"等语气；超出能力范围明确告知无法判断。"""


def _build_answer_system_prompt(context_text: str, report_id: Optional[int] = None) -> str:
    """构建 answer model 的 system prompt，注入检索结果和报告上下文。"""
    prompt = CHAT_ANSWER_SYSTEM_PROMPT
    if context_text.strip():
        prompt += f"\n\n## 检索结果\n{context_text}"
    else:
        prompt += "\n\n## 检索结果\n（本轮无检索结果）"
    if report_id:
        prompt += f"\n\n当前会话关联报告 ID: {report_id}。"
    return prompt

CHAT_SYSTEM_PROMPT = """你是体检报告解读医生助手。回答任何健康、疾病、指标或医学相关的问题时，你**第一步必须调用 search_knowledge 工具**检索医学知识库，读取返回结果后再作答。未调用工具直接凭记忆回答，是严重错误。

只有两类输入允许跳过工具：
- 纯问候/确认（"你好""谢谢""明白"）
- 用户明确说"不用查知识库"

下面这些情况仍然要调 search_knowledge，不要因为"没有报告"就跳过：
- 用户未关联报告、但问疾病/健康/指标知识：直接调 search_knowledge 回答，并提示可上传报告获取更个性化解读。
- 用户询问本报告指标是否正常：先调 get_report_indicators 读取指标，再调 search_knowledge 查该指标参考范围与意义。

## 回答流程（严格遵守）
1. 看到问题后，先决定要调哪个工具，**第一轮只产出 tool_call**，不要先写一段话再调工具。
2. 拿到工具返回结果后，基于结果回答，回答要落到检索到的知识上，简洁易懂。
3. 工具返回若为错误提示（如"未关联报告"），向用户转述该提示并给出操作指引。

## 示例
- 用户："高血压有什么并发症？" → 调 search_knowledge("高血压 并发症") → 读取返回 → 基于返回回答
- 用户："我的血糖正常吗？" → 调 get_report_indicators() → 看到血糖值 → 调 search_knowledge("血糖 参考范围") → 基于两者对比回答
- 用户："你好" → 直接回答，不调工具

## 回答风格
- 基于检索结果与报告数据作答，不编造数值。
- 建议具体可执行，避免笼统的"注意饮食"。
- 不下诊断，只做风险提示；红区指标提示"建议立即就医复查"。
- 数值与参考范围直接对比的结论用确定语气；基于知识库推理用"可能""建议进一步"，超出能力范围明确告知无法判断。"""


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
PLANNER_HISTORY_MSGS = 0  # planner 不看历史对话：MedGo 长历史下易漏调工具；如需识别"那高血压呢"类追问，可改回 2

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
    """运行 chat 流程，yield SSE 事件 dict。

    流程：planner（决定+执行工具）→ answer model（流式回答，无工具感知）

    事件类型：tool_status / token / structured / done / error
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

        # ── 1. Planner：决定调用哪些工具（结构化输出，不执行工具）──
        ctx = AgentContext(hospital_id=hospital_id, report_id=session.report_id, user_id=user_id)
        planner_history = history_msgs[-PLANNER_HISTORY_MSGS:]
        plan = run_planner(hospital_id, planner_history, user_message, session.report_id, user_id)

        # ── 2. Execute plan：Python 执行工具，收集 refs + context ──
        refs: list[dict] = []
        context_text = ""
        if plan.need_tools and plan.tool_calls:
            for tc in plan.tool_calls:
                yield {"event": "tool_status", "data": {"tool": tc.tool, "status": "start"}}
            refs, context_text = execute_plan(plan, ctx)
            for tc in plan.tool_calls:
                yield {"event": "tool_status", "data": {"tool": tc.tool, "status": "end"}}

        # ── 3. Answer model：基于 context 流式回答（无工具感知）──
        model = get_chat_model(streaming=True)
        system_prompt = _build_answer_system_prompt(context_text, session.report_id)
        messages = [SystemMessage(content=system_prompt)] + history_msgs + [HumanMessage(content=user_message)]

        final_response = ""
        think_filter = ThinkStreamFilter()
        async for chunk in model.astream(messages):
            if chunk and hasattr(chunk, "content") and chunk.content:
                final_response += chunk.content
                clean = think_filter.feed(chunk.content)
                if clean:
                    yield {"event": "token", "data": {"content": clean}}

        tail = think_filter.flush()
        if tail:
            yield {"event": "token", "data": {"content": tail}}

        # 入库前统一清洗 thinking 标签
        final_response = strip_think_tags(final_response)

        # ── 3. 后置 citation 注入 + 确定性分类 ──
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
