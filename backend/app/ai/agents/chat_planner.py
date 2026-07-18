"""Chat 工具调度器（planner）。

根据用户问题决定调用哪些工具，在 Python 中执行工具并累积检索结果，
供 chat LLM 在回答时引用。chat LLM 本身对工具无感知。

流程：
1. run_planner(history_msgs, user_message, ctx) → ChatPlan（结构化输出，不执行工具）
2. execute_plan(plan, ctx) → (refs, context_text)（Python 直接执行工具）
3. chat_graph.py 的 run_chat_agent 把 context_text 注入 answer model 的 system prompt，
   并用 refs 做 citation 注入

注：planner 用 with_structured_output 输出 ChatPlan，不绑定 tools，避免弱模型
反复调用工具导致 context 溢出。工具执行在 Python 中完成，无 LLM 参与。
"""
import json
import logging
from typing import List, Optional

from langchain.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.llm import get_chat_model, _guarded
from app.ai.agents.tools import AgentContext
from app.config import settings
from app.core.database import get_session

logger = logging.getLogger("app.planner")

PLANNER_SYSTEM_PROMPT = """你是体检报告解读系统的工具调度器。你的唯一职责是根据用户问题决定需要调用哪些工具，不做任何医学回答。

## 决策规则
- 用户问疾病/健康/症状/指标知识 → 调用 search_knowledge（用疾病名或症状名作 query）
- 用户问本报告指标是否正常 → 先调 get_report_indicators 读指标，再调 search_knowledge 查该指标参考范围
- 用户问指标历史趋势 → 调 get_indicator_history（传 item_name）
- 用户问历年报告概况 → 调 get_user_history_reports
- 用户问三色分级规则 → 调 get_triage_rules
- 用户问本报告概况 → 调 get_report_summary
- 纯问候/确认（"你好""谢谢""明白"）→ need_tools=false，不调任何工具

## 用户未关联报告
- 仍可调 search_knowledge（查疾病知识库不依赖报告）
- 不要调 get_report_indicators / get_report_summary（会返回错误）

## 约束
- 你只负责规划，禁止输出任何医学分析、解读、建议
- tool_calls 中的每个条目只需填 tool 名和该工具需要的参数，其余留空"""


class PlannedToolCall(BaseModel):
    tool: str = Field(description="工具名: search_knowledge | get_report_indicators | get_report_summary | get_user_history_reports | get_indicator_history | get_triage_rules")
    query: Optional[str] = Field(default=None, description="search_knowledge 的搜索词")
    item_name: Optional[str] = Field(default=None, description="get_indicator_history 的指标名")
    limit: Optional[int] = Field(default=None, description="get_user_history_reports 的返回条数")


class ChatPlan(BaseModel):
    """Planner 的结构化输出"""
    need_tools: bool = Field(default=False, description="是否需要调用工具")
    tool_calls: list[PlannedToolCall] = Field(default_factory=list, description="要执行的工具调用列表")
    summary: str = Field(default="", description="规划简述")


async def run_planner(
    hospital_id: str,
    history_msgs: list,
    user_message: str,
    report_id: Optional[int],
    user_id: Optional[int],
) -> ChatPlan:
    """运行 planner：用结构化输出决定调用哪些工具（不执行工具）。

    返回 ChatPlan 实例，tool_calls 为空列表表示无需工具。
    MedGo 调用经 medgo_sem 收口。
    """
    model = get_chat_model(streaming=False)
    model.max_tokens = 16384  # 16k：MedGo 长历史下 structured-output JSON 容易超 512 被截断，导致 fallback 空计划
    model.temperature = 0.0
    structured = model.with_structured_output(ChatPlan)
    messages = [SystemMessage(content=PLANNER_SYSTEM_PROMPT)] + history_msgs + [HumanMessage(content=user_message)]
    try:
        plan = await _guarded(structured.ainvoke(messages))
        return plan
    except Exception as e:
        logger.warning("planner failed: %s, returning empty plan", e)
        return ChatPlan(need_tools=False, tool_calls=[], summary=f"planner error: {e}")


def _execute_search_knowledge(ctx: AgentContext, query: str,
                               category_ids=None, top_k=None) -> tuple[list[dict], str]:
    from app.ai import rag as ai_rag
    results = ai_rag.search(ctx.hospital_id, query, category_ids=category_ids, top_k=top_k)
    refs = [{"entry_id": r.entry_id, "title": r.title, "source": r.source} for r in results]
    lines = []
    for r in results:
        parts = [r.title]
        if r.content:
            parts.append(r.content[:500])
        lines.append(" - ".join(parts))
    return refs, "\n".join(lines) if lines else ""


def _execute_get_report_indicators(ctx: AgentContext) -> tuple[list, str]:
    report_id = ctx.report_id
    if not report_id:
        return [], "（未关联报告，无法获取指标）"
    db = get_session(f"hospital_{ctx.hospital_id}")
    try:
        rows = db.execute(
            text("SELECT id, item_name, item_name_standard, result_value, unit, "
                 "ref_range_low, ref_range_high FROM report_indicator WHERE report_id = :rid ORDER BY id"),
            {"rid": report_id},
        ).fetchall()
    finally:
        db.close()
    lines = []
    for r in rows:
        lines.append(f"{r[1]}: {r[3]}{r[4] or ''} (参考 {r[5]}-{r[6]})")
    return [], "\n".join(lines) if lines else "（无指标数据）"


def _execute_get_report_summary(ctx: AgentContext) -> tuple[list, str]:
    report_id = ctx.report_id
    if not report_id:
        return [], "（未关联报告）"
    db = get_session(f"hospital_{ctx.hospital_id}")
    try:
        row = db.execute(
            text("SELECT r.report_date, r.name, i.overall_level, i.red_count, i.yellow_count, i.green_count "
                 "FROM report_info r LEFT JOIN report_interpretation i ON i.report_id = r.id "
                 "WHERE r.id = :rid"),
            {"rid": report_id},
        ).fetchone()
    finally:
        db.close()
    if not row:
        return [], "（无报告概况）"
    entries = [f"报告日期: {row[0]}", f"名称: {row[1]}", f"整体判定: {row[2]}",
               f"红区: {row[3]}", f"黄区: {row[4]}", f"绿区: {row[5]}"]
    return [], "\n".join(entries)


def _execute_get_user_history_reports(ctx: AgentContext, limit: int = 5) -> tuple[list, str]:
    user_id = ctx.user_id
    if not user_id:
        return [], "（无法识别用户）"
    db = get_session(f"hospital_{ctx.hospital_id}")
    try:
        rows = db.execute(
            text("SELECT r.id, r.report_date, i.overall_level "
                 "FROM report_info r LEFT JOIN report_interpretation i ON i.report_id = r.id "
                 "WHERE r.user_id = :uid ORDER BY r.report_date DESC LIMIT :lim"),
            {"uid": user_id, "lim": limit},
        ).fetchall()
    finally:
        db.close()
    lines = [f"{r[1]}: {r[2]}" for r in rows]
    return [], "\n".join(lines) if lines else "（无历史报告）"


def _execute_get_indicator_history(ctx: AgentContext, item_name: str) -> tuple[list, str]:
    user_id = ctx.user_id
    if not user_id:
        return [], "（无法识别用户）"
    db = get_session(f"hospital_{ctx.hospital_id}")
    try:
        rows = db.execute(
            text("SELECT ri.report_date, ind.result_value, ind.unit "
                 "FROM report_indicator ind JOIN report_info ri ON ind.report_id = ri.id "
                 "WHERE ri.user_id = :uid AND ind.item_name = :name ORDER BY ri.report_date ASC"),
            {"uid": user_id, "name": item_name},
        ).fetchall()
    finally:
        db.close()
    lines = [f"{r[0]}: {r[1]}{r[2] or ''}" for r in rows]
    return [], "\n".join(lines) if lines else f"（无 {item_name} 历史数据）"


def _execute_get_triage_rules(ctx: AgentContext) -> tuple[list, str]:
    db = get_session(f"hospital_{ctx.hospital_id}")
    try:
        rows = db.execute(
            text("SELECT rule_name, indicator_code, conditions, color_level "
                 "FROM triage_rule WHERE is_active = 1 ORDER BY priority"),
        ).fetchall()
    finally:
        db.close()
    lines = [f"{r[0]}: {r[1]} → {r[3]}" for r in rows]
    return [], "\n".join(lines) if lines else "（无三色分级规则）"


def execute_plan(plan: ChatPlan, ctx: AgentContext) -> tuple[list[dict], str]:
    """执行 ChatPlan 中的工具调用，返回 (refs, context_text)。

    refs: search_knowledge 返回的引用列表 [{entry_id, title, source}]
    context_text: 所有工具结果拼接的文本，注入 answer model 的 system prompt
    """
    all_refs: list[dict] = []
    context_blocks: list[str] = []

    for tc in plan.tool_calls:
        tool = tc.tool
        try:
            if tool == "search_knowledge" and tc.query:
                refs, text = _execute_search_knowledge(ctx, tc.query, top_k=tc.limit)
                all_refs.extend(refs)
                if text:
                    context_blocks.append(f"### search_knowledge(\"{tc.query}\")\n{text}")
            elif tool == "get_report_indicators":
                _, text = _execute_get_report_indicators(ctx)
                context_blocks.append(f"### {tool}\n{text}")
            elif tool == "get_report_summary":
                _, text = _execute_get_report_summary(ctx)
                context_blocks.append(f"### {tool}\n{text}")
            elif tool == "get_user_history_reports":
                _, text = _execute_get_user_history_reports(ctx, limit=tc.limit or 5)
                context_blocks.append(f"### {tool}\n{text}")
            elif tool == "get_indicator_history" and tc.item_name:
                _, text = _execute_get_indicator_history(ctx, tc.item_name)
                context_blocks.append(f"### {tool}(\"{tc.item_name}\")\n{text}")
            elif tool == "get_triage_rules":
                _, text = _execute_get_triage_rules(ctx)
                context_blocks.append(f"### {tool}\n{text}")
            else:
                logger.warning("execute_plan: unknown/incomplete tool call: %s", tool)
        except Exception as e:
            logger.warning("execute_plan: tool %s failed: %s", tool, e)
            context_blocks.append(f"### {tool}\n（执行失败: {e}）")

    return all_refs, "\n\n".join(context_blocks)