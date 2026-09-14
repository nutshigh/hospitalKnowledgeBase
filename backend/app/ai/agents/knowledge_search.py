"""确定性知识检索 planner / executor(2026-09-13)。

原「AI 解读」知识检索步是模型自驱的 ReAct 工具 agent(`build_interp_agent`):
模型决定调哪些工具/什么词 → 观察 → 再决定, 终止权在模型手里。实测模型只是把异常
指标名原样照抄为 query(无选词增值), 却曾因单步生成退化循环(repeated tool-call
数组无 maxItems)卡死约 30 分钟并导致 RabbitMQ 消息丢失(report 31)。

本模块用纯函数替代:
- `plan_knowledge_search`: 确定性产出检索计划(每异常指标一条, 含保守 fallback)。
- `execute_search_plan`: 确定性单趟执行(首选空则依次试 fallback, 仍空跳过)。
**绝不调用 LLM**。
"""
import logging
import re
from dataclasses import dataclass, field

from app.ai import rag as ai_rag
from app.core.term_normalizer import (
    _clean_textual_noise,
    normalize_item_name,
    resolve_canonical,
)

logger = logging.getLogger("app.interp")

_MAX_FALLBACKS = 3
_PAREN_RE = re.compile(r"[（(][^)）]*[)）]")


@dataclass
class SearchCall:
    """单条检索计划: indicator 为归属指标名(用于日志/追溯), query 为首选检索词。"""
    indicator: str
    query: str
    fallback_queries: list[str] = field(default_factory=list)


def _fallback_candidates(item_name: str, query: str) -> list[str]:
    """生成保守 fallback 候选: 归一化名 / 标准名 / 剥前后缀 / 去括号 / 斜杠拆分。

    仅在首选 query 0 结果时依次尝试; 去重、去掉与 query 相同项、上限 3 个。
    """
    candidates: list[str] = []

    def _add(c) -> None:
        if not c:
            return
        c = str(c).strip()
        if c and c != query and c not in candidates:
            candidates.append(c)

    try:
        norm, _ = normalize_item_name(item_name)
        _add(norm)
    except Exception:
        pass
    try:
        term = resolve_canonical(item_name)
        if term:
            _add(term.standard)
    except Exception:
        pass
    try:
        cleaned = _clean_textual_noise(item_name)
        _add(_PAREN_RE.sub("", cleaned))
        if "/" in cleaned:
            for part in cleaned.split("/"):
                _add(part)
                try:
                    pnorm, _ = normalize_item_name(part)
                    _add(pnorm)
                except Exception:
                    pass
    except Exception:
        pass

    return candidates[:_MAX_FALLBACKS]


def plan_knowledge_search(abnormal_indicators: list[dict]) -> list[SearchCall]:
    """异常指标 → 检索计划(纯函数)。

    按 item_name 去重(filter_abnormal 可能带重复), 每条 query = item_name,
    与 indicator_judgment.item_name 保持一致。
    """
    plan: list[SearchCall] = []
    seen: set = set()
    for ind in abnormal_indicators or []:
        name = (ind or {}).get("item_name")
        if not name:
            continue
        name = str(name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        plan.append(SearchCall(
            indicator=name, query=name, fallback_queries=_fallback_candidates(name, name),
        ))
    return plan


def _refs_dict_from_search_results(results) -> dict:
    """检索结果 → knowledge_results / references dict(形状与旧工具结果完全一致)。

    文档: {entry_id(int): {entry_id,title,source,content}}
    知识图谱: {"kg:<title>": {entry_id: None,title,source:"knowledge_graph",content}}
    """
    refs: dict = {}
    for r in results or []:
        eid = getattr(r, "entry_id", None)
        source = getattr(r, "source", "document") or "document"
        content = getattr(r, "content", "") or ""
        title = getattr(r, "title", "") or ""
        if eid is not None:
            refs[eid] = {"entry_id": eid, "title": title, "source": source, "content": content}
        elif source == "knowledge_graph":
            kg_key = f"kg:{title}"
            if kg_key not in refs:
                refs[kg_key] = {"entry_id": None, "title": title,
                                "source": "knowledge_graph", "content": content}
    return refs


def execute_search_plan(hospital_id: str, plan: list[SearchCall], search_fn=None) -> dict:
    """确定性执行检索计划(单趟, 无循环)。

    每条首选 query 为空时依次试 fallback; 命中即合并并停止该指标; 全部为空则跳过。
    单次检索抛异常只记日志, 不影响其它指标(空结果合法, 绝不冒泡)。
    """
    search = search_fn or ai_rag.search
    merged: dict = {}
    for call in plan:
        for q in [call.query, *call.fallback_queries]:
            try:
                results = search(hospital_id, q)
            except Exception as e:
                logger.warning(
                    "knowledge search failed indicator=%s query=%s: %s",
                    call.indicator, q, e,
                )
                continue
            if results:
                merged.update(_refs_dict_from_search_results(results))
                if q != call.query:
                    logger.info(
                        "knowledge search fallback hit indicator=%s query=%s via=%s",
                        call.indicator, call.query, q,
                    )
                break
        else:
            logger.info("knowledge search empty indicator=%s query=%s",
                        call.indicator, call.query)
    return merged


def run_knowledge_search(hospital_id: str, abnormal_indicators: list[dict]) -> dict:
    """解读图节点入口: 计划 + 执行, 返回 knowledge_results。"""
    if not abnormal_indicators:
        return {}
    return execute_search_plan(hospital_id, plan_knowledge_search(abnormal_indicators))
