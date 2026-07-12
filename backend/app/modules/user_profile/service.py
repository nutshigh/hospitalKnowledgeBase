import logging
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.report.models import ReportInfo, ReportIndicator
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from app.modules.user_profile.comparison import (
    match_indicators, compute_delta, judge_status, trend_direction,
    build_comparison_prompt,
)
from app.ai.llm import get_chat_model
from app.ai.agents.think_filter import strip_think_tags

logger = logging.getLogger(__name__)

MAX_HISTORY_REPORTS = 100
TOP_INDICATORS_DEFAULT = 10
TOP_ABNORMAL_FOR_PROMPT = 5
STATUS_STABLE_PCT = 5


def _auto_select_baseline(db: Session, user_id: int, report_id: int) -> Optional[ReportInfo]:
    """选 report_date 早于本报告且最接近的那一份,fallback created_at。"""
    current = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not current:
        return None
    q = db.query(ReportInfo).filter(
        ReportInfo.user_id == user_id,
        ReportInfo.id != report_id,
    )
    if current.report_date:
        q = q.filter(ReportInfo.report_date < current.report_date)
        q = q.order_by(ReportInfo.report_date.desc())
    else:
        q = q.order_by(ReportInfo.created_at.desc())
    return q.first()


def get_overview(db: Session, user_id: int) -> dict:
    """档案页主数据:总览 + 指标走势 + 异常分布。"""
    reports = db.query(ReportInfo).filter(
        ReportInfo.user_id == user_id,
    ).order_by(ReportInfo.report_date.asc()).all()
    if not reports:
        return {"user_summary": None, "indicator_trends": [], "abnormal_distribution": []}

    report_ids = [r.id for r in reports]
    indicators = db.query(ReportIndicator).filter(
        ReportIndicator.report_id.in_(report_ids),
    ).all()
    report_map = {r.id: r for r in reports}

    judgments_by_indicator_id = {}
    if indicators:
        judgments = db.query(IndicatorJudgment).filter(
            IndicatorJudgment.indicator_id.in_([i.id for i in indicators]),
        ).all()
        judgments_by_indicator_id = {j.indicator_id: j for j in judgments}

    by_key = {}
    for ind in indicators:
        try:
            float(str(ind.result_value).strip())
        except (TypeError, ValueError):
            continue
        key = ind.item_name_standard or ind.item_name
        if not key:
            continue
        if key not in by_key:
            by_key[key] = {
                "item_name_standard": ind.item_name_standard,
                "item_name": ind.item_name,
                "unit": ind.unit,
                "points": [],
            }
        judgment = judgments_by_indicator_id.get(ind.id)
        by_key[key]["points"].append({
            "report_id": ind.report_id,
            "report_date": report_map[ind.report_id].report_date.isoformat() if report_map[ind.report_id].report_date else None,
            "value": float(str(ind.result_value).strip()),
            "color": judgment.color_level if judgment else None,
        })

    for v in by_key.values():
        v["points"].sort(key=lambda p: p["report_date"] or "")
        v["trend_direction"] = trend_direction(v["points"])
        v["latest_deviation"] = v["points"][-1].get("color") if v["points"] else None

    abnormal_dist_q = text("""
        SELECT ij.item_name, rind.item_name_standard, ij.color_level, COUNT(*) as cnt
        FROM indicator_judgment ij
        JOIN report_interpretation ri2 ON ij.interpretation_id = ri2.id
        JOIN report_info ri ON ri2.report_id = ri.id
        JOIN report_indicator rind ON ij.indicator_id = rind.id
        WHERE ri.user_id = :uid AND ij.color_level IN ('red', 'yellow')
        GROUP BY ij.item_name, rind.item_name_standard, ij.color_level
    """)
    rows = db.execute(abnormal_dist_q, {"uid": user_id}).fetchall()
    grouped = {}
    for r in rows:
        key = r.item_name_standard or r.item_name
        if not key:
            continue
        if key not in grouped:
            grouped[key] = {"item_name_standard": key, "red_count": 0, "yellow_count": 0, "last_color": "green"}
        if r.color_level == "red":
            grouped[key]["red_count"] += r.cnt
        elif r.color_level == "yellow":
            grouped[key]["yellow_count"] += r.cnt
        if r.color_level in ("red", "yellow"):
            grouped[key]["last_color"] = r.color_level
    abnormal_distribution = sorted(
        grouped.values(),
        key=lambda x: (x["red_count"], x["yellow_count"]),
        reverse=True,
    )[:20]

    latest = reports[-1]
    latest_interp = db.query(ReportInterpretation).filter_by(report_id=latest.id).first()
    summary = {
        "total_reports": len(reports),
        "earliest_date": reports[0].report_date.isoformat() if reports[0].report_date else None,
        "latest_date": latest.report_date.isoformat() if latest.report_date else None,
        "latest_overall_level": latest_interp.overall_level if latest_interp else None,
        "latest_red": latest_interp.red_count if latest_interp else 0,
        "latest_yellow": latest_interp.yellow_count if latest_interp else 0,
        "latest_green": latest_interp.green_count if latest_interp else 0,
        "baseline_date": None,
    }
    baseline = _auto_select_baseline(db, user_id, latest.id)
    if baseline:
        summary["baseline_date"] = baseline.report_date.isoformat() if baseline.report_date else None

    trends_sorted = sorted(
        by_key.values(),
        key=lambda x: (
            0 if x.get("latest_deviation") in ("red", "yellow") else 1,
            -abs(max([p["value"] for p in x["points"]], default=0) - min([p["value"] for p in x["points"]], default=0)),
        ),
    )
    return {
        "user_summary": summary,
        "indicator_trends": trends_sorted,
        "abnormal_distribution": abnormal_distribution,
    }


def _build_indicator_diff(db: Session, current: ReportInfo, baseline: ReportInfo) -> dict:
    """组装对比明细(current / baseline / delta_summary / indicators / only_in_*)。"""
    cur_inds = db.query(ReportIndicator).filter_by(report_id=current.id).all()
    base_inds = db.query(ReportIndicator).filter_by(report_id=baseline.id).all()

    cur_judgments = {j.indicator_id: j for j in db.query(IndicatorJudgment).join(
        ReportInterpretation, IndicatorJudgment.interpretation_id == ReportInterpretation.id
    ).filter(ReportInterpretation.report_id == current.id).all()}
    base_judgments = {j.indicator_id: j for j in db.query(IndicatorJudgment).join(
        ReportInterpretation, IndicatorJudgment.interpretation_id == ReportInterpretation.id
    ).filter(ReportInterpretation.report_id == baseline.id).all()}

    cur_dicts = [
        {**_indicator_to_dict(i), "color_level": cur_judgments[i.id].color_level if i.id in cur_judgments else None}
        for i in cur_inds
    ]
    base_dicts = [
        {**_indicator_to_dict(i), "color_level": base_judgments[i.id].color_level if i.id in base_judgments else None}
        for i in base_inds
    ]

    matches = match_indicators(cur_dicts, base_dicts)
    indicators_diff = []
    matched_stds = {m["item_name_standard"] for m in matches if m["item_name_standard"]}
    matched_raw_names = {m["item_name"] for m in matches if not m["item_name_standard"]}
    only_in_current = []
    only_in_baseline = []

    for ind in cur_inds:
        if ind.item_name_standard:
            already_matched = ind.item_name_standard in matched_stds
        else:
            already_matched = ind.item_name in matched_raw_names
        if not already_matched:
            only_in_current.append({
                "item_name": ind.item_name,
                "item_name_standard": ind.item_name_standard,
                "current_value": ind.result_value,
                "unit": ind.unit,
            })
    for ind in base_inds:
        if ind.item_name_standard:
            already_matched = ind.item_name_standard in matched_stds
        else:
            already_matched = ind.item_name in matched_raw_names
        if not already_matched:
            only_in_baseline.append({
                "item_name": ind.item_name,
                "item_name_standard": ind.item_name_standard,
                "baseline_value": ind.result_value,
                "unit": ind.unit,
            })

    for m in matches:
        delta = compute_delta(m["current_value"], m["baseline_value"])
        entry = {
            "item_name_standard": m["item_name_standard"],
            "item_name": m["item_name"],
            "current_value": m["current_value"],
            "baseline_value": m["baseline_value"],
            "unit": m["unit"],
            "current_color": m["current_color"],
            "baseline_color": m["baseline_color"],
            "delta": None,
            "delta_pct": None,
            "status": None,
        }
        if delta is not None:
            entry["delta"], entry["delta_pct"] = delta
            entry["status"] = judge_status(delta[1])
        indicators_diff.append(entry)

    cur_interp = db.query(ReportInterpretation).filter_by(report_id=current.id).first()
    base_interp = db.query(ReportInterpretation).filter_by(report_id=baseline.id).first()

    return {
        "current": {
            "report_id": current.id,
            "report_date": current.report_date.isoformat() if current.report_date else None,
            "overall_level": cur_interp.overall_level if cur_interp else None,
            "red_count": cur_interp.red_count if cur_interp else 0,
            "yellow_count": cur_interp.yellow_count if cur_interp else 0,
            "green_count": cur_interp.green_count if cur_interp else 0,
        },
        "baseline": {
            "report_id": baseline.id,
            "report_date": baseline.report_date.isoformat() if baseline.report_date else None,
            "overall_level": base_interp.overall_level if base_interp else None,
            "red_count": base_interp.red_count if base_interp else 0,
            "yellow_count": base_interp.yellow_count if base_interp else 0,
            "green_count": base_interp.green_count if base_interp else 0,
        },
        "delta_summary": {
            "red_delta": (cur_interp.red_count if cur_interp else 0) - (base_interp.red_count if base_interp else 0),
            "yellow_delta": (cur_interp.yellow_count if cur_interp else 0) - (base_interp.yellow_count if base_interp else 0),
            "green_delta": (cur_interp.green_count if cur_interp else 0) - (base_interp.green_count if base_interp else 0),
        },
        "indicators": indicators_diff,
        "only_in_current": only_in_current,
        "only_in_baseline": only_in_baseline,
        "_current_report_obj": current,
        "_baseline_report_obj": baseline,
        "_current_interp": cur_interp,
    }


def _indicator_to_dict(ind):
    return {
        "item_name": ind.item_name,
        "item_name_standard": ind.item_name_standard,
        "result_value": ind.result_value,
        "unit": ind.unit,
    }


def _filter_abnormal_top(diff_result: dict) -> list[dict]:
    """筛出给 prompt 用的 top 异常指标 (red 优先, 黄次之, 同色 |delta| 降序)。"""
    indicators = diff_result["indicators"]
    def sort_key(x):
        cur_color = x.get("current_color") or "green"
        color_pri = 0 if cur_color == "red" else (1 if cur_color == "yellow" else 2)
        delta_abs = abs(x.get("delta") or 0)
        return (color_pri, -delta_abs)
    return sorted(indicators, key=sort_key)[:TOP_ABNORMAL_FOR_PROMPT]


def get_comparison(db: Session, user_id: int, report_id: int,
                   baseline_id: Optional[int] = None) -> dict:
    """对比接口主入口。附带 ai_summary(走缓存命中逻辑)。"""
    current = db.query(ReportInfo).filter_by(id=report_id, user_id=user_id).first()
    if not current:
        return {}
    if baseline_id:
        baseline = db.query(ReportInfo).filter_by(id=baseline_id, user_id=user_id).first()
        if not baseline:
            return {}
    else:
        baseline = _auto_select_baseline(db, user_id, report_id)
        if not baseline:
            diff = {
                "current": {
                    "report_id": current.id,
                    "report_date": current.report_date.isoformat() if current.report_date else None,
                    "overall_level": None, "red_count": 0, "yellow_count": 0, "green_count": 0,
                },
                "baseline": None,
                "delta_summary": {"red_delta": 0, "yellow_delta": 0, "green_delta": 0},
                "indicators": [],
                "only_in_current": [],
                "only_in_baseline": [],
                "ai_summary": "",
                "ai_summary_cached": False,
            }
            return diff

    diff = _build_indicator_diff(db, current, baseline)
    interp = diff.get("_current_interp")
    ai_summary = ""
    cached = False
    if interp:
        if interp.comparison_summary and interp.comparison_baseline_id == baseline.id:
            ai_summary = interp.comparison_summary or ""
            cached = True

    diff_out = {k: v for k, v in diff.items() if not k.startswith("_")}
    diff_out["ai_summary"] = ai_summary
    diff_out["ai_summary_cached"] = cached
    return diff_out


def get_ai_summary(db: Session, user_id: int, report_id: int, baseline_id: int) -> tuple[str, bool]:
    """读缓存或调 LLM 实时生成。实时生成不写回缓存。"""
    current = db.query(ReportInfo).filter_by(id=report_id, user_id=user_id).first()
    baseline = db.query(ReportInfo).filter_by(id=baseline_id, user_id=user_id).first()
    if not current or not baseline:
        return "", False
    interp = db.query(ReportInterpretation).filter_by(report_id=report_id).first()
    if interp and interp.comparison_summary and interp.comparison_baseline_id == baseline_id:
        return interp.comparison_summary, True

    diff = _build_indicator_diff(db, current, baseline)
    top_abnormal = _filter_abnormal_top(diff)
    prompt = build_comparison_prompt(
        diff["current"], diff["baseline"], diff["indicators"], top_abnormal,
    )
    summary = _call_llm_for_summary(prompt)
    return summary, False


def _call_llm_for_summary(prompt: str) -> str:
    """调用 MedGo 生成小结。失败返回空串并记 warning。"""
    try:
        model = get_chat_model(streaming=False)
        resp = model.invoke([("user", prompt)], max_tokens=512)
        return strip_think_tags(resp.content or "")
    except Exception as e:
        logger.warning("comparison summary LLM call failed: %s", e)
        return ""


def try_generate_comparison_summary(db: Session, report_id: int) -> None:
    """worker 钩子:解读完成后调一次,生成 AI 小结并写回缓存。

    - 用户历史报告不足 2 份 -> 跳过
    - 缓存已有且 baseline 匹配 -> 跳过
    - LLM 失败 -> logger.warning,不报错,不阻塞主流程
    """
    current = db.query(ReportInfo).filter_by(id=report_id).first()
    if not current:
        return
    interp = db.query(ReportInterpretation).filter_by(report_id=report_id).first()
    if not interp:
        return
    if interp.comparison_summary and interp.comparison_baseline_id:
        return

    baseline = _auto_select_baseline(db, current.user_id, report_id)
    if not baseline:
        return

    diff = _build_indicator_diff(db, current, baseline)
    top_abnormal = _filter_abnormal_top(diff)
    prompt = build_comparison_prompt(
        diff["current"], diff["baseline"], diff["indicators"], top_abnormal,
    )
    try:
        model = get_chat_model(streaming=False)
        resp = model.invoke([("user", prompt)], max_tokens=512)
        summary = strip_think_tags(resp.content or "")
        if summary:
            interp.comparison_summary = summary
            interp.comparison_baseline_id = baseline.id
            db.commit()
    except Exception as e:
        logger.warning("comparison summary generation failed: %s", e)