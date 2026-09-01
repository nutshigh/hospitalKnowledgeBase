from dataclasses import dataclass
from typing import List, Optional

from langchain.tools import tool, ToolRuntime, BaseTool
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai import rag as ai_rag
from app.core.database import get_session


@dataclass
class AgentContext:
    """工具运行时上下文，通过 agent.invoke(context=...) 注入。

    工具每次调用通过 hospital_id 从连接池拿独立 session，避免 async agent
    并发执行工具时共享 session 导致 MySQL 协议包错乱。
    report_id/user_id 由调用方（chat agent / interp agent）注入，避免弱模型
    在工具调用入参中漏填这两个必填值。
    """
    hospital_id: str
    report_id: Optional[int] = None
    user_id: Optional[str] = None
    name: Optional[str] = None


def _db(hospital_id: str) -> Session:
    """从连接池为该医院取一个独立 session（调用方负责 close）。"""
    return get_session(f"hospital_{hospital_id}")


@tool
def search_knowledge(
    query: str,
    runtime: ToolRuntime[AgentContext],
    category_ids: Optional[List[int]] = None,
    top_k: Optional[int] = None,
) -> list[dict]:
    """搜索医学知识库，返回相关知识条目。用于查找指标解读、疾病知识、健康建议等医学信息。
    Args:
        query: 搜索查询，如"空腹血糖偏高"或"ALT 升高原因"
        category_ids: 可选，限定知识分类 ID 列表
        top_k: 可选，返回条数上限
    Returns:
        知识条目列表，每项含 entry_id/title/content/score/source
    """
    ctx = runtime.context
    results = ai_rag.search(ctx.hospital_id, query, category_ids=category_ids, top_k=top_k)
    return [{"entry_id": r.entry_id, "title": r.title, "content": r.content, "score": r.score, "source": r.source} for r in results]


@tool
def get_report_indicators(runtime: ToolRuntime[AgentContext]) -> list[dict]:
    """获取当前会话关联体检报告的所有结构化指标数据。
    Returns:
        指标列表，每项含 item_name/result_value/unit/ref_range_low/ref_range_high
    """
    report_id = runtime.context.report_id
    if not report_id:
        return [{"error": "当前会话未关联报告，请先上传或选择报告"}]
    db = _db(runtime.context.hospital_id)
    try:
        rows = db.execute(
            text("SELECT id, item_name, item_name_standard, result_value, unit, "
                 "ref_range_low, ref_range_high FROM report_indicator WHERE report_id = :rid ORDER BY id"),
            {"rid": report_id},
        ).fetchall()
    finally:
        db.close()
    return [{"id": r[0], "item_name": r[1], "item_name_standard": r[2],
             "result_value": r[3], "unit": r[4],
             "ref_range_low": r[5], "ref_range_high": r[6]} for r in rows]


@tool
def get_report_summary(runtime: ToolRuntime[AgentContext]) -> dict:
    """获取当前会话关联报告的概览信息（报告日期、整体判定、红黄绿计数、异常指标明细）。
    Returns:
        含 report_date/overall_level/red_count/yellow_count/green_count/abnormal_indicators 的 dict
    """
    report_id = runtime.context.report_id
    if not report_id:
        return {"error": "当前会话未关联报告，请先上传或选择报告"}
    db = _db(runtime.context.hospital_id)
    try:
        row = db.execute(
            text("SELECT r.report_date, r.name, i.overall_level, i.red_count, i.yellow_count, i.green_count, "
                 "i.id AS interp_id "
                 "FROM report_info r LEFT JOIN report_interpretation i ON i.report_id = r.id "
                 "WHERE r.id = :rid"),
            {"rid": report_id},
        ).fetchone()
        abnormal = []
        if row and row[6]:
            abnormal = db.execute(
                text("SELECT item_name, result_value, deviation, color_level "
                     "FROM indicator_judgment WHERE interpretation_id = :iid "
                     "AND color_level != 'green' ORDER BY color_level, item_name"),
                {"iid": row[6]},
            ).fetchall()
    finally:
        db.close()
    if not row:
        return {}
    return {"report_date": str(row[0]) if row[0] else None, "name": row[1],
            "overall_level": row[2], "red_count": row[3],
            "yellow_count": row[4], "green_count": row[5],
            "abnormal_indicators": [
                {"item_name": r[0], "result_value": r[1], "deviation": r[2], "color_level": r[3]}
                for r in abnormal
            ]}


@tool
def get_user_history_reports(runtime: ToolRuntime[AgentContext], limit: int = 5) -> list[dict]:
    """获取当前用户的历年体检报告概览，用于趋势对比。
    Args:
        limit: 返回条数，默认 5
    Returns:
         报告列表，每项含 report_id/report_date/overall_level
    """
    user_id = runtime.context.user_id
    name = runtime.context.name
    if not user_id or not name:
        return [{"error": "无法识别当前用户"}]
    db = _db(runtime.context.hospital_id)
    try:
        rows = db.execute(
            text("SELECT r.id, r.report_date, i.overall_level "
                 "FROM report_info r LEFT JOIN report_interpretation i ON i.report_id = r.id "
                 "WHERE r.user_id = :uid AND r.name = :nm ORDER BY r.report_date DESC LIMIT :lim"),
            {"uid": user_id, "nm": name, "lim": limit},
        ).fetchall()
    finally:
        db.close()
    return [{"report_id": r[0], "report_date": str(r[1]) if r[1] else None,
             "overall_level": r[2]} for r in rows]


@tool
def get_indicator_history(item_name: str, runtime: ToolRuntime[AgentContext]) -> list[dict]:
    """获取当前用户某指标的历史数值，用于趋势研判。
    Args:
        item_name: 指标名称
    Returns:
        历史数值列表，每项含 date/value/unit
    """
    user_id = runtime.context.user_id
    name = runtime.context.name
    if not user_id or not name:
        return [{"error": "无法识别当前用户"}]
    db = _db(runtime.context.hospital_id)
    try:
        rows = db.execute(
            text("SELECT ri.report_date, ind.result_value, ind.unit "
                 "FROM report_indicator ind "
                 "JOIN report_info ri ON ind.report_id = ri.id "
                 "WHERE ri.user_id = :uid AND ri.name = :nm AND ind.item_name = :name "
                 "ORDER BY ri.report_date ASC"),
            {"uid": user_id, "nm": name, "name": item_name},
        ).fetchall()
    finally:
        db.close()
    return [{"date": str(r[0]) if r[0] else None, "value": r[1], "unit": r[2]} for r in rows]


@tool
def get_triage_rules(runtime: ToolRuntime[AgentContext]) -> list[dict]:
    """获取当前生效的三色分级规则，了解哪些指标阈值会被判定为红区/黄区。
    Returns:
        规则列表，每项含 rule_name/indicator_code/conditions/color_level
    """
    db = _db(runtime.context.hospital_id)
    try:
        rows = db.execute(
            text("SELECT rule_name, indicator_code, conditions, color_level "
                 "FROM triage_rule WHERE is_active = 1 ORDER BY priority"),
        ).fetchall()
    finally:
        db.close()
    return [{"rule_name": r[0], "indicator_code": r[1],
             "conditions": r[2], "color_level": r[3]} for r in rows]


CHAT_TOOLS: list[BaseTool] = [
    search_knowledge, get_report_indicators, get_report_summary,
    get_user_history_reports, get_indicator_history, get_triage_rules,
]

INTERP_TOOLS: list[BaseTool] = [search_knowledge, get_triage_rules]
