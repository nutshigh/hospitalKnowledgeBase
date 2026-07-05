import json
import logging
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
from app.ai.agents.think_filter import strip_think_tags
from app.ai.agents.citation_matcher import inject_citations
from app.ai.agents.judge_graph import run_judge
from app.config import settings

logger = logging.getLogger(__name__)

INTERP_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，为体检者撰写易懂的指标解读和健康建议。
规则:
1. 绿区指标一笔带过，重点解读红区和黄区
2. 建议具体可执行，避免笼统的"注意饮食"
3. 不诊断疾病，只做健康风险提示
4. 危急值指标提示"建议立即就医复查"

确定性分级规则：
- definite：基于指标数值与参考范围的直接对比判断
- probable：基于知识库推理但非直接数值判断
- refused：信息不足或超出助手能力范围，不做猜测

输出要求：
- 没有知识库或报告数据支撑的结论性陈述视为编造，禁止输出
- certainty 级别必须与结论性质匹配

你有以下工具可用：
- search_knowledge: 搜索医学知识库（对每个异常指标都应查询相关知识）
- get_triage_rules: 获取三色分级规则

对每个异常指标生成 explanation（解读）、suggestion（建议）、certainty（确定性）、citations（引用列表，每项含 ref_id/entry_id/title/source）。引用来源由系统自动标注，你只需确保结论基于工具返回的知识。"""


class Citation(BaseModel):
    """引用条目"""
    ref_id: int = Field(description="内联标记编号，如 [1] 对应 ref_id=1")
    entry_id: Optional[int] = Field(default=None, description="知识条目 ID，知识图谱结果为 null")
    title: str = Field(default="", description="知识条目标题")
    source: str = Field(default="document", description="来源类型: document | knowledge_graph")


class InterpBatchItem(BaseModel):
    """单指标的解读结果"""
    indicator_id: int = Field(description="异常指标 ID")
    explanation: str = Field(description="指标解读文字，含内联 [n] 标注")
    suggestion: str = Field(description="健康建议文字，含内联 [n] 标注")
    certainty: str = Field(description="确定性级别: definite | probable | refused")
    certainty_reason: str = Field(default="", description="确定性判定理由")
    citations: list[Citation] = Field(default_factory=list, description="引用列表")


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
    judge_result: dict
    judge_retry_count: int


class InterpAgentState(AgentState):
    knowledge_results: NotRequired[dict]


def _extract_refs_dict_from_tool_result(result) -> dict:
    """从 ToolMessage 或 Command 解析 search_knowledge 返回的 {key: ref}（含 content）"""
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
                        source = r.get("source", "document")
                        content = r.get("content", "")
                        title = r.get("title", "")
                        if eid is not None:
                            refs_dict[eid] = {"entry_id": eid, "title": title, "source": source, "content": content}
                        elif source == "knowledge_graph":
                            kg_key = f"kg:{title}"
                            if kg_key not in refs_dict:
                                refs_dict[kg_key] = {"entry_id": None, "title": title, "source": "knowledge_graph", "content": content}
            except (json.JSONDecodeError, TypeError):
                pass
    return refs_dict


class InterpKnowledgeMiddleware(AgentMiddleware):
    """拦截 search_knowledge，把 {entry_id→ref} 累积到 state.knowledge_results"""

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        result = handler(request)
        if request.tool_call["name"] == "search_knowledge":
            refs_dict = _extract_refs_dict_from_tool_result(result)
            if refs_dict:
                if isinstance(result, Command):
                    update = dict(result.update or {})
                    update["knowledge_results"] = refs_dict
                    return Command(update=update)
                return Command(update={"knowledge_results": refs_dict, "messages": [result]})
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
    """把结构化输出映射到 explanations/refs，并做后置 citation 注入。

    citations 不再依赖 LLM 输出的 [n] 标记，而是由 inject_citations
    基于 embedding 相似度自动匹配 explanation/suggestion 中的句子到来源 chunk。
    """
    explanations = {}
    mapped_refs = {}
    all_sources = list(knowledge_results.values())

    for item in structured.items:
        raw_explanation = strip_think_tags(item.explanation)
        raw_suggestion = strip_think_tags(item.suggestion)

        # 后置 citation 注入：对 explanation 和 suggestion 分别做
        annotated_explanation, cite_explanation = inject_citations(raw_explanation, all_sources)
        annotated_suggestion, cite_suggestion = inject_citations(raw_suggestion, all_sources)

        # 合并两个文本的 citations（重新编号）
        combined_citations = _merge_citations(cite_explanation, cite_suggestion)

        explanations[item.indicator_id] = {
            "explanation": annotated_explanation,
            "suggestion": annotated_suggestion,
            "certainty": item.certainty,
            "certainty_reason": item.certainty_reason,
        }
        mapped_refs[item.indicator_id] = combined_citations

    # 补全结构化未覆盖的异常指标
    for ind in abnormal_indicators:
        iid = ind["indicator_id"]
        if iid not in explanations:
            explanations[iid] = {"explanation": "", "suggestion": "", "certainty": "refused", "certainty_reason": "未生成解读"}
        if iid not in mapped_refs:
            mapped_refs[iid] = all_sources

    return explanations, mapped_refs


def _merge_citations(cite_a: list[dict], cite_b: list[dict]) -> list[dict]:
    """合并两段文本的 citations，重新连续编号。"""
    merged = []
    seen_keys = set()
    ref_map = {}  # old_ref_id -> new_ref_id

    for cite in cite_a + cite_b:
        # 用 entry_id + title 做去重 key
        key = (cite.get("entry_id"), cite.get("title"), cite.get("source"))
        if key not in seen_keys:
            seen_keys.add(key)
            new_ref_id = len(merged) + 1
            ref_map[cite["ref_id"]] = new_ref_id
            merged.append({
                "ref_id": new_ref_id,
                "entry_id": cite.get("entry_id"),
                "title": cite.get("title", ""),
                "source": cite.get("source", "document"),
                "content": cite.get("content", ""),
            })
        else:
            # 找到已存在的 ref_id
            for m in merged:
                if (m.get("entry_id"), m.get("title"), m.get("source")) == key:
                    ref_map[cite["ref_id"]] = m["ref_id"]
                    break

    return merged


def _agent_batch(state: InterpState, build_agent_fn, db: Session) -> dict:
    """agent_batch 节点核心逻辑（模块级，便于测试）"""
    if not state["abnormal_indicators"]:
        return {"agent_explanations": {}, "knowledge_refs": {}, "judge_retry_count": 0}

    agent = build_agent_fn()
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
- indicator_id（指标 ID）
- explanation（解读文字，含内联 [n] 标注）
- suggestion（建议文字，含内联 [n] 标注）
- certainty（确定性: definite/probable/refused）
- certainty_reason（确定性理由）
- citations（引用列表，每项含 ref_id/entry_id/title/source）"""

    # 重试时追加 judge 反馈
    retry_count = state.get("judge_retry_count", 0)
    if retry_count > 0:
        judge_result = state.get("judge_result", {})
        issues = judge_result.get("issues", [])
        suggestions = judge_result.get("suggestions", "")
        issues_text = "\n".join(f"- {issue}" for issue in issues)
        user_content += f"""

## 质量审核反馈（第 {retry_count} 次重试）
上次生成存在以下问题：
{issues_text}

改进要求：
{suggestions}

请修正以上问题，重新生成解读结果。确保每个结论都有 [n] 引用标注，且 citations 列表完整。"""

    result = agent.invoke(
        {"messages": [HumanMessage(content=user_content)]},
        config={"recursion_limit": settings.AGENT_MAX_ITERATIONS * 2},
        context=AgentContext(hospital_id=state["hospital_id"]),
    )

    structured = result.get("structured_response")
    knowledge_results = result.get("knowledge_results", {})

    if structured is None:
        logger.warning(
            "interp_graph agent_batch got no structured_response for report_id=%s",
            state["report_id"],
        )
        structured = InterpBatchResult(items=[])

    explanations, mapped_refs = _map_structured_to_explanations(
        structured, knowledge_results, state["abnormal_indicators"],
    )
    return {
        "agent_explanations": explanations,
        "knowledge_refs": mapped_refs,
        "judge_retry_count": retry_count + 1,
    }


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
        return _agent_batch(state, build_interp_agent, db)

    def judge(state: InterpState) -> dict:
        """Judge 审核 agent_batch 的输出。"""
        if not state.get("abnormal_indicators"):
            return {"judge_result": {"passed": True, "issues": [], "suggestions": ""}}
        judge_result = run_judge(state)
        return {"judge_result": judge_result}

    def error_handler(state: InterpState) -> dict:
        """Judge 未通过且重试次数用尽，标记失败，留待人工处理。"""
        from app.modules.interpretation.models import ReportInterpretation

        interp = db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == state["report_id"],
            ReportInterpretation.status == "processing",
        ).first()
        if interp:
            interp.retry_count += 1
            interp.status = "failed"
            interp.summary_text = f"Judge 审核未通过（重试 {state['judge_retry_count']} 次）: " + \
                                  "; ".join(state["judge_result"].get("issues", []))
            db.commit()
        logger.warning("Report %s judge failed after %d retries, needs manual review",
                       state["report_id"], state["judge_retry_count"])
        return {}

    def after_judge(state: InterpState) -> str:
        """条件边：根据 judge 结果决定下一步"""
        judge_result = state.get("judge_result", {})
        if judge_result.get("passed", True):
            return "persist"
        if state.get("judge_retry_count", 0) >= settings.JUDGE_MAX_RETRIES:
            return "error_handler"
        return "agent_batch"

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
                certainty=exp_data.get("certainty", ""),
                certainty_reason=exp_data.get("certainty_reason", ""),
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
    g.add_node("judge", judge)
    g.add_node("error_handler", error_handler)
    g.add_node("persist", persist)
    g.set_entry_point("load_indicators")
    g.add_edge("load_indicators", "run_rules")
    g.add_edge("run_rules", "filter_abnormal")
    g.add_edge("filter_abnormal", "agent_batch")
    g.add_edge("agent_batch", "judge")
    g.add_conditional_edges("judge", after_judge, {
        "persist": "persist",
        "agent_batch": "agent_batch",
        "error_handler": "error_handler",
    })
    g.add_edge("persist", END)
    g.add_edge("error_handler", END)
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
            "judge_result": {},
            "judge_retry_count": 0,
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
