import json
import logging
from datetime import datetime
from typing import Annotated, List, Optional, TypedDict

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.graph import StateGraph, END
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing_extensions import NotRequired

from app.ai.agents.tools import AgentContext, INTERP_TOOLS
from app.ai.agents.think_filter import strip_think_tags
from app.ai.agents.citation_matcher import inject_citations
from app.ai.agents.judge_graph import run_judge
from app.ai.llm import get_chat_model
from app.config import settings

logger = logging.getLogger(__name__)

SEARCH_SYSTEM_PROMPT = """你是医学知识检索工具的执行器。你**唯一**能做的是对每个异常指标调用 search_knowledge 工具。

强制规则：
- 禁止输出任何医学分析、解读、建议或诊断——你只负责检索，不做判断
- 对用户列出的每一个指标名，必须分别调用一次 search_knowledge（使用指标名作为查询词）
- 如果某个指标第一次没搜到好结果，换一个查询词再试一次
- 所有指标检索完成后，用 ConfirmSchema 汇报已完成的指标列表
- 绝对不要直接回答用户——你必须使用工具

如果用户没有列出指标，直接用 ConfirmSchema 返回空列表。"""


class ConfirmSchema(BaseModel):
    searched_indicators: list[str] = Field(default_factory=list, description="已完成检索的指标名称列表")


GENERATE_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。基于提供的医学知识和异常指标，撰写一份结构化的综合解读报告。

撰写规则：
1. overall_summary：1-2 段，整体健康概况，不诊断疾病。
2. abnormal_focus：每个红/黄区指标一段，说明偏离方向、可能原因、临床意义。绿区指标不在此节展开。
3. trend_note：若有历年数据说明趋势变化；无则留空字符串。
4. suggestions：具体可执行建议（饮食/运动/复查等），避免笼统的"注意饮食"。
5. risk_alert：红区指标提示"建议立即就医复查"；无红区则留空字符串。

约束：
- 不要 [n] 引用标记（系统会自动基于知识库来源注入）
- 仅基于提供的数据与知识库内容，不编造具体数值
- 5 个字段均要返回（无内容时填空字符串）
"""

SECTIONS = ["overall_summary", "abnormal_focus", "trend_note", "suggestions", "risk_alert"]


class InterpretationReport(BaseModel):
    overall_summary: str = ""
    abnormal_focus: str = ""
    trend_note: str = ""
    suggestions: str = ""
    risk_alert: str = ""


class Citation(BaseModel):
    ref_id: int
    entry_id: Optional[int] = None
    title: str = ""
    source: str = "document"


def _merge_knowledge_results(current: dict, update: dict) -> dict:
    merged = dict(current or {})
    merged.update(update or {})
    return merged


class InterpAgentState(AgentState):
    knowledge_results: Annotated[dict, _merge_knowledge_results]


class InterpState(TypedDict):
    hospital_id: str
    report_id: int
    user_id: int
    indicators: List[dict]
    judgments: List[dict]
    abnormal_indicators: List[dict]
    knowledge_results: dict
    report: InterpretationReport
    references: list
    overall_level: str
    red_count: int
    yellow_count: int
    green_count: int
    judge_result: dict
    judge_retry_count: int


def _extract_refs_dict_from_tool_result(result) -> dict:
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
    model = get_chat_model(streaming=False)
    model.max_tokens = 512
    return create_agent(
        model=model,
        tools=INTERP_TOOLS,
        system_prompt=SEARCH_SYSTEM_PROMPT,
        response_format=ToolStrategy(ConfirmSchema),
        middleware=[InterpKnowledgeMiddleware()],
        state_schema=InterpAgentState,
    )


def build_report_model():
    model = get_chat_model(streaming=False)
    model.max_tokens = 4096
    return model


def _merge_citations(cite_a: list[dict], cite_b: list[dict]) -> list[dict]:
    merged = []
    seen_keys = set()
    for cite in (cite_a or []) + (cite_b or []):
        key = (cite.get("entry_id"), cite.get("title"), cite.get("source"))
        if key not in seen_keys:
            seen_keys.add(key)
            new_ref_id = len(merged) + 1
            merged.append({
                "ref_id": new_ref_id,
                "entry_id": cite.get("entry_id"),
                "title": cite.get("title", ""),
                "source": cite.get("source", "document"),
                "content": cite.get("content", ""),
            })
    return merged


def _fetch_trend(user_id: int, db: Session) -> str:
    if not user_id:
        return ""
    try:
        rows = db.execute(
            text("SELECT ri.report_date, ind.item_name, ind.result_value, ind.unit "
                 "FROM report_indicator ind JOIN report_info ri ON ind.report_id = ri.id "
                 "WHERE ri.user_id = :uid ORDER BY ri.report_date ASC"),
            {"uid": user_id},
        ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    by_date: dict = {}
    for r in rows:
        d = str(r[0]) if r[0] else "未知"
        by_date.setdefault(d, []).append(f"{r[1]}={r[2]}{r[3] or ''}")
    for d, vals in by_date.items():
        lines.append(f"{d}: " + ", ".join(vals[:8]))
    return "\n".join(lines)


def _generate_report(state: InterpState, db: Session) -> dict:
    abnormal = state.get("abnormal_indicators", []) or []
    if not abnormal:
        return {
            "report": InterpretationReport(),
            "references": [],
            "judge_retry_count": state.get("judge_retry_count", 0),
        }

    knowledge = list((state.get("knowledge_results") or {}).values())
    trend = _fetch_trend(state.get("user_id"), db)

    abnormal_lines = []
    for ind in abnormal:
        abnormal_lines.append(
            f"- {ind.get('item_name')}: 值 {ind.get('result_value')}{ind.get('unit','')}, "
            f"参考 {ind.get('ref_range_low','-')}-{ind.get('ref_range_high','-')}, "
            f"{ind.get('deviation')}, {ind.get('color_level')}区"
        )
    abnormal_text = "\n".join(abnormal_lines)

    knowledge_blocks = []
    for k in knowledge:
        knowledge_blocks.append(
            f"- [来源] title={k.get('title','')}, source={k.get('source','document')}\n  {k.get('content','')[:500]}"
        )
    knowledge_text = "\n".join(knowledge_blocks) or "（无知识库结果）"

    user_content = f"""请基于以下数据撰写综合解读报告（5 节）：

## 报告概况
- 整体判定: {state.get('overall_level','green')}区
- 红区 {state.get('red_count',0)} 项, 黄区 {state.get('yellow_count',0)} 项, 绿区 {state.get('green_count',0)} 项

## 异常指标
{abnormal_text}

## 检索到的医学知识
{knowledge_text}

## 历年趋势
{trend or '（首份报告，无历史对比）'}

按 system 提示的 5 节字段返回 JSON。"""

    model = build_report_model()
    resp = model.invoke([("system", GENERATE_SYSTEM_PROMPT), ("user", user_content)]).content
    import re as _re
    from json_repair import repair_json
    match = _re.search(r'\{[\s\S]*\}', resp or "")
    if not match:
        logger.warning("generate_report: no JSON in LLM response, fallback to empty report")
        report_raw = {}
    else:
        try:
            report_raw = json.loads(match.group())
        except json.JSONDecodeError:
            try:
                report_raw = json.loads(repair_json(match.group()))
            except Exception:
                report_raw = {}
    report = InterpretationReport(
        overall_summary=strip_think_tags(report_raw.get("overall_summary", "")),
        abnormal_focus=strip_think_tags(report_raw.get("abnormal_focus", "")),
        trend_note=strip_think_tags(report_raw.get("trend_note", "")),
        suggestions=strip_think_tags(report_raw.get("suggestions", "")),
        risk_alert=strip_think_tags(report_raw.get("risk_alert", "")),
    )

    refs_all: list[dict] = []
    summaries = {}
    for field in SECTIONS:
        text_val = getattr(report, field)
        annotated, citations = inject_citations(text_val, knowledge)
        summaries[field] = annotated
        refs_all = _merge_citations(refs_all, citations)

    final_report = InterpretationReport(**summaries)
    retry_count = state.get("judge_retry_count", 0)
    return {
        "report": final_report,
        "references": refs_all,
        "judge_retry_count": retry_count + 1,
    }


def build_interp_graph(hospital_id: str, db: Session):
    from app.modules.report.models import ReportInfo

    def load_indicators(state: InterpState) -> dict:
        report_id = state["report_id"]
        row = db.execute(
            text("SELECT id, user_id FROM report_info WHERE id = :rid"),
            {"rid": report_id},
        ).fetchone()
        user_id = row[1] if row else 0
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
        return {"indicators": indicators, "user_id": user_id}

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
            color_level = result.color_level
            if deviation == "normal":
                try:
                    val = float(ind["result_value"] or 0)
                    ref_high = float(ind["ref_range_high"] or 0)
                    ref_low = float(ind["ref_range_low"] or 0)
                    if ref_high and val > ref_high:
                        deviation = "high"
                        if color_level == "green":
                            color_level = "yellow"
                    elif ref_low and val < ref_low:
                        deviation = "low"
                        if color_level == "green":
                            color_level = "yellow"
                except (ValueError, TypeError):
                    pass

            judgments.append({
                "indicator_id": ind["id"],
                "item_name": ind["item_name"],
                "result_value": ind["result_value"],
                "deviation": deviation,
                "color_level": color_level,
                "matched_rule_id": result.matched_rule_id,
            })

            if color_level == "red":
                red_count += 1
            elif color_level == "yellow":
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
        by_id = {i["id"]: i for i in state["indicators"]}
        abnormal = [
            {**j, **{
                "unit": by_id.get(j["indicator_id"], {}).get("unit"),
                "ref_range_low": by_id.get(j["indicator_id"], {}).get("ref_range_low"),
                "ref_range_high": by_id.get(j["indicator_id"], {}).get("ref_range_high"),
            }}
            for j in state["judgments"]
            if j["color_level"] in ("red", "yellow")
        ]
        return {"abnormal_indicators": abnormal}

    def agent_search_knowledge(state: InterpState) -> dict:
        if not state.get("abnormal_indicators"):
            return {"knowledge_results": {}}
        names = [ind["item_name"] for ind in state["abnormal_indicators"]]
        agent = build_interp_agent()
        all_results = {}
        batch_size = 10
        for i in range(0, len(names), batch_size):
            batch = names[i:i + batch_size]
            user_content = "\n".join(f"- {n}" for n in batch)
            result = agent.invoke(
                {"messages": [HumanMessage(content=user_content)]},
                config={"recursion_limit": settings.AGENT_MAX_ITERATIONS * 2},
                context=AgentContext(hospital_id=state["hospital_id"]),
            )
            batch_results = result.get("knowledge_results", {}) or {}
            all_results.update(batch_results)
        return {"knowledge_results": all_results}

    def generate_report(state: InterpState) -> dict:
        return _generate_report(state, db)

    def judge(state: InterpState) -> dict:
        if not state.get("abnormal_indicators"):
            return {"judge_result": {"passed": True, "issues": [], "suggestions": ""}}
        return {"judge_result": run_judge(state)}

    def after_judge(state: InterpState) -> str:
        judge_result = state.get("judge_result", {})
        if judge_result.get("passed", True):
            return "persist"
        if state.get("judge_retry_count", 0) >= settings.JUDGE_MAX_RETRIES:
            return "persist_with_note"
        return "generate_report"

    def persist(state: InterpState) -> dict:
        from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
        from app.core.rabbitmq import rabbitmq, TaskMessage

        report_id = state["report_id"]
        db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id,
        ).delete()
        db.commit()

        interp = ReportInterpretation(report_id=report_id, status="processing")
        db.add(interp)
        db.commit()
        db.refresh(interp)

        report = state.get("report") or InterpretationReport()
        references = state.get("references", []) or []
        interp.summary_text = report.model_dump_json()
        interp.summary_refs = references
        judge_result = state.get("judge_result", {})
        if not judge_result.get("passed", True):
            issues = "; ".join(judge_result.get("issues", [])[:3])
            interp.quality_note = (issues or "审核未通过")[:255]

        for j in state["judgments"]:
            db.add(IndicatorJudgment(
                interpretation_id=interp.id,
                indicator_id=j["indicator_id"],
                item_name=j["item_name"],
                result_value=j["result_value"],
                deviation=j["deviation"],
                color_level=j["color_level"],
                matched_rule_id=j["matched_rule_id"],
                explanation=None, suggestion=None, knowledge_refs=None,
                certainty=None, certainty_reason=None,
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

    def persist_with_note(state: InterpState) -> dict:
        return persist(state)

    g = StateGraph(InterpState)
    g.add_node("load_indicators", load_indicators)
    g.add_node("run_rules", run_rules)
    g.add_node("filter_abnormal", filter_abnormal)
    g.add_node("agent_search_knowledge", agent_search_knowledge)
    g.add_node("generate_report", generate_report)
    g.add_node("judge", judge)
    g.add_node("persist", persist)
    g.add_node("persist_with_note", persist_with_note)
    g.set_entry_point("load_indicators")
    g.add_edge("load_indicators", "run_rules")
    g.add_edge("run_rules", "filter_abnormal")
    g.add_edge("filter_abnormal", "agent_search_knowledge")
    g.add_edge("agent_search_knowledge", "generate_report")
    g.add_edge("generate_report", "judge")
    g.add_conditional_edges("judge", after_judge, {
        "persist": "persist",
        "persist_with_note": "persist_with_note",
        "generate_report": "generate_report",
    })
    g.add_edge("persist", END)
    g.add_edge("persist_with_note", END)
    return g.compile()


def run_interpretation_agent(hospital_id: str, db: Session, report_id: int) -> dict:
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
            "user_id": 0,
            "indicators": [],
            "judgments": [],
            "abnormal_indicators": [],
            "knowledge_results": {},
            "report": InterpretationReport(),
            "references": [],
            "overall_level": "green",
            "red_count": 0, "yellow_count": 0, "green_count": 0,
            "judge_result": {},
            "judge_retry_count": 0,
        })
        return final_state
    except Exception as e:
        interp = db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id,
            ReportInterpretation.status == "processing",
        ).first()
        if interp:
            interp.retry_count += 1
            interp.status = "failed" if interp.retry_count >= 3 else "pending"
            db.commit()
        raise
