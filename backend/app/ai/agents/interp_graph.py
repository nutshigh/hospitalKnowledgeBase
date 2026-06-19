import json
import re
from typing import TypedDict, List
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.llm import get_chat_model
from app.ai.agents.tools import INTERP_TOOLS
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


class InterpBatchResult(TypedDict):
    """单指标的解读结果"""
    indicator_id: int
    explanation: str
    suggestion: str


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


def build_interp_graph(hospital_id: str, db: Session):
    """构造 interpretation Agent 的 LangGraph StateGraph"""

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

        tools = INTERP_TOOLS
        model = get_chat_model(streaming=False).bind_tools(tools)
        tools_by_name = {t.name: t for t in tools}

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

对每个指标调用 search_knowledge 查询相关知识，然后输出 JSON 数组，每个元素：
{{"indicator_id": int, "explanation": "解读文字", "suggestion": "建议文字", "knowledge_ref_ids": [int, ...]}}
其中 knowledge_ref_ids 是你在解读该指标时实际引用的 search_knowledge 结果中的 entry_id 列表。"""

        messages = [
            SystemMessage(content=INTERP_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        max_iter = settings.AGENT_MAX_ITERATIONS
        knowledge_refs = {}
        iterations_used = 0
        for i in range(max_iter):
            iterations_used = i + 1
            resp = model.invoke(messages)
            messages.append(resp)
            if not (hasattr(resp, "tool_calls") and resp.tool_calls):
                break
            for call in resp.tool_calls:
                tool = tools_by_name.get(call["name"])
                if not tool:
                    continue
                result = tool.invoke(call["args"])
                if call["name"] == "search_knowledge" and isinstance(result, list):
                    for r in result:
                        ref_item = {"entry_id": r.get("entry_id"), "title": r.get("title")}
                        for ind in state["abnormal_indicators"]:
                            iid = ind["indicator_id"]
                            if iid not in knowledge_refs:
                                knowledge_refs[iid] = []
                            if ref_item not in knowledge_refs[iid]:
                                knowledge_refs[iid].append(ref_item)
                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False),
                    "tool_call_id": call["id"],
                })

        if iterations_used >= max_iter and hasattr(resp, "tool_calls") and resp.tool_calls:
            import logging
            logging.getLogger(__name__).warning(
                "interp_graph agent_batch exhausted %d iterations without final answer for report_id=%s",
                max_iter, state["report_id"]
            )

        explanations = {}
        mapped_refs = {}
        raw = resp.content if hasattr(resp, "content") else str(resp)
        try:
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                for item in parsed:
                    iid = item.get("indicator_id")
                    if iid:
                        explanations[iid] = {
                            "explanation": item.get("explanation", ""),
                            "suggestion": item.get("suggestion", ""),
                        }
                        ref_ids = set(item.get("knowledge_ref_ids", []))
                        mapped_refs[iid] = [
                            r for r in (knowledge_refs.get(iid, []) or [])
                            if r.get("entry_id") in ref_ids
                        ] or knowledge_refs.get(iid, [])
        except (json.JSONDecodeError, AttributeError):
            pass

        for ind in state["abnormal_indicators"]:
            iid = ind["indicator_id"]
            if iid not in explanations:
                explanations[iid] = {"explanation": "", "suggestion": ""}
            if iid not in mapped_refs:
                mapped_refs[iid] = knowledge_refs.get(iid, [])

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
