import asyncio
import hashlib
import json
import logging
import re
from datetime import date, datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.report.models import ReportInfo, ReportIndicator
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from app.core.term_normalizer import is_child_item, normalize_item_name
from app.modules.user_profile.comparison import (
    compute_delta, trend_direction, _try_float, build_change_prompt,
)
from app.ai.llm import get_chat_model, _guarded
from app.ai.agents.think_filter import strip_think_tags
from app.config import settings

logger = logging.getLogger(__name__)


def _auto_select_baseline(db: Session, user_id: str, name: str, report_id: int) -> Optional[ReportInfo]:
    """选基线报告。优先取 report_date 早于当前报告且最接近的一份;fallback created_at。

    2026-09-02 起允许选任意报告:当前报告为该用户最早一份(无更早 report_date)时,
    不再返回 None(否则前端对比卡片整卡隐藏、连下拉框都看不到),而是退化为该用户
    report_date 与该报告日期最接近的另一份报告作默认基线。全部无日期则取最近创建。
    """
    current = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not current:
        return None
    others = db.query(ReportInfo).filter(
        ReportInfo.user_id == user_id,
        ReportInfo.name == name,
        ReportInfo.id != report_id,
    ).all()
    if not others:
        return None

    def _ct(o: ReportInfo):
        return o.created_at or datetime.min

    if current.report_date is None:
        return max(others, key=_ct)

    dated = [o for o in others if o.report_date]
    if not dated:
        return max(others, key=_ct)

    earlier = [o for o in dated if o.report_date < current.report_date]
    if earlier:
        return max(earlier, key=lambda o: (o.report_date, _ct(o)))

    min_days = min(abs((o.report_date - current.report_date).days) for o in dated)
    nearest = [o for o in dated if abs((o.report_date - current.report_date).days) == min_days]
    return max(nearest, key=_ct)


def _split_item_name_collisions(items: list[dict]) -> list[dict]:
    """防线:同一系列内若同一份报告出现多个不同 item_name(标准名吞噬造成的脏数据,
    如血常规子项被并入父项),按 (item_name, unit) 拆成独立系列,避免异量纲数值画成一条线。

    同名重复行(如同一报告同一指标多次测量)不拆 —— 交给 distinct-report 门/展示语义处理。
    """
    out: list[dict] = []
    for item in items:
        names_by_report: dict = {}
        for p in item["points"]:
            names_by_report.setdefault(p["report_id"], set()).add(p.get("item_name"))
        if not any(len(names) > 1 for names in names_by_report.values()):
            out.append(item)
            continue
        logger.warning(
            "indicator series %r mixes distinct item_name within one report; "
            "splitting by item_name/unit", item.get("item_name"),
        )
        groups: dict = {}
        for p in item["points"]:
            gkey = (p.get("item_name"), p.get("unit"))
            g = groups.setdefault(gkey, {
                "item_name_standard": normalize_item_name(gkey[0])[0],
                "item_name": gkey[0],
                "unit": gkey[1],
                "points": [],
            })
            g["points"].append(p)
        for g in groups.values():
            g["points"].sort(key=lambda p: (p["report_date"] is not None, p["report_date"] or ""))
            out.append(g)
    return out


def get_overview(db: Session, user_id: str, name: str) -> dict:
    """档案页主数据:总览 + 指标走势(仅最近 PROFILE_TREND_REPORT_LIMIT 份报告)+ 异常分布。"""
    reports = db.query(ReportInfo).filter(
        ReportInfo.user_id == user_id,
        ReportInfo.name == name,
    ).order_by(ReportInfo.report_date.asc()).all()
    if not reports:
        return {"user_summary": None, "indicator_trends": [], "abnormal_distribution": []}

    trend_report_ids = [r.id for r in reports[-settings.PROFILE_TREND_REPORT_LIMIT:]]
    indicators = db.query(ReportIndicator).filter(
        ReportIndicator.report_id.in_(trend_report_ids),
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
        if is_child_item(ind.item_name or ""):
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
            "item_name": ind.item_name,
            "unit": ind.unit,
        })

    trend_items = _split_item_name_collisions(list(by_key.values()))
    for v in trend_items:
        v["points"].sort(key=lambda p: (p["report_date"] is not None, p["report_date"] or ""))
        v["trend_direction"] = trend_direction(v["points"])
        v["latest_deviation"] = v["points"][-1].get("color") if v["points"] else None

    trend_items = [v for v in trend_items if _has_abnormal(v["points"])]

    abnormal_dist_q = text("""
        SELECT ij.item_name, rind.item_name_standard, ij.color_level, COUNT(*) as cnt
        FROM indicator_judgment ij
        JOIN report_interpretation ri2 ON ij.interpretation_id = ri2.id
        JOIN report_info ri ON ri2.report_id = ri.id
        JOIN report_indicator rind ON ij.indicator_id = rind.id
        WHERE ri.user_id = :uid AND ri.name = :name AND ij.color_level IN ('red', 'yellow')
        GROUP BY ij.item_name, rind.item_name_standard, ij.color_level
    """)
    rows = db.execute(abnormal_dist_q, {"uid": user_id, "name": name}).fetchall()
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
    baseline = _auto_select_baseline(db, user_id, name, latest.id)
    if baseline:
        summary["baseline_date"] = baseline.report_date.isoformat() if baseline.report_date else None

    trends_sorted = sorted(trend_items, key=_trend_sort_key)
    return {
        "user_summary": summary,
        "indicator_trends": trends_sorted[:settings.PROFILE_TREND_MAX_ITEMS],
        "abnormal_distribution": abnormal_distribution,
    }


# ===========================================================================
# 跨报告健康变化总览(2026-09-09):自动对比最近 N 份已完成解读的报告
# ===========================================================================

def empty_change_overview(covered: int) -> dict:
    """不足 2 份可对比报告时的降级响应。covered = 该锚定已解读报告数。"""
    return {
        "reports": [],
        "covered": covered,
        "reason": "insufficient",
        "key_indicators": [],
        "summary": None,
        "cached": False,
    }


def _change_window(db: Session, user_id: str, name: str) -> list:
    """返回 [(ReportInfo, ReportInterpretation), ...] 升序,仅 completed,取最近 N 份。

    report_date 升序,None 视为最旧放最前(与 /overview 口径一致);取末
    PROFILE_TREND_REPORT_LIMIT 份。
    """
    rows = (
        db.query(ReportInfo, ReportInterpretation)
        .join(ReportInterpretation, ReportInterpretation.report_id == ReportInfo.id)
        .filter(
            ReportInfo.user_id == user_id,
            ReportInfo.name == name,
            ReportInterpretation.status == "completed",
        )
        .all()
    )
    window = []
    for report, interp in rows:
        window.append((report, interp))

    def _key(pair):
        report = pair[0]
        return (report.report_date is not None, report.report_date or date.min, report.id)

    ordered = sorted(window, key=_key)
    return ordered[-settings.PROFILE_TREND_REPORT_LIMIT:]


def _report_header(pair) -> dict:
    report, interp = pair
    return {
        "report_id": report.id,
        "report_date": report.report_date.isoformat() if report.report_date else None,
        "overall_level": interp.overall_level,
        "red_count": interp.red_count,
        "yellow_count": interp.yellow_count,
        "green_count": interp.green_count,
    }


def _window_std_fingerprint(db: Session, report_ids: list[int]) -> str:
    """窗口指标标准名指纹:窗口内所有 (indicator_id, item_name_standard) 的确定性摘要。

    09-10 标准名回填 / 未来词表变更都会改写 item_name_standard 而 report/interp id 不变;
    指纹保证这类变化使旧缓存失效重算。行序无关,输出稳定。
    """
    if not report_ids:
        return ""
    rows = db.query(ReportIndicator).filter(
        ReportIndicator.report_id.in_(report_ids),
    ).all()
    pairs = sorted((ind.id, ind.item_name_standard or "") for ind in rows)
    return hashlib.md5(repr(pairs).encode("utf-8")).hexdigest()


def _read_cached_overview(interp: Optional[ReportInterpretation], window: list,
                          fingerprint: str) -> Optional[dict]:
    """列内容为 JSON、signature 与当前窗口一致且 fingerprint 匹配窗口指标标准名
    → 返回 payload;否则 None(含旧纯文本、旧格式无 fingerprint 的缓存)。"""
    if not interp or not interp.comparison_summary:
        return None
    try:
        data = json.loads(interp.comparison_summary)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sig = [{"report_id": pair[0].id, "interp_id": pair[1].id} for pair in window]
    if data.get("signature") != sig:
        return None
    if data.get("fingerprint") != fingerprint:
        return None
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _series(db: Session, window: list) -> list[dict]:
    """按 item_name_standard 聚合窗口内数值指标为 points;value 保留原始字符串。"""
    report_ids = [pair[0].id for pair in window]
    rid2date = {pair[0].id: _report_header(pair)["report_date"] for pair in window}
    inds = db.query(ReportIndicator).filter(ReportIndicator.report_id.in_(report_ids)).all()
    colors: dict = {}
    if inds:
        judgments = (
            db.query(IndicatorJudgment)
            .join(ReportInterpretation,
                  IndicatorJudgment.interpretation_id == ReportInterpretation.id)
            .filter(ReportInterpretation.report_id.in_(report_ids))
            .all()
        )
        colors = {j.indicator_id: j.color_level for j in judgments}

    by_key: dict = {}
    for ind in inds:
        if _try_float(ind.result_value) is None:
            continue
        key = ind.item_name_standard or ind.item_name
        if not key:
            continue
        item = by_key.setdefault(key, {
            "item_name": key,
            "item_name_standard": ind.item_name_standard,
            "unit": ind.unit,
            "points": [],
        })
        item["points"].append({
            "report_id": ind.report_id,
            "report_date": rid2date.get(ind.report_id),
            "value": str(ind.result_value).strip(),
            "color": colors.get(ind.id),
            "item_name": ind.item_name,
            "unit": ind.unit,
        })
    for item in by_key.values():
        item["points"].sort(key=lambda p: (p["report_date"] is not None, p["report_date"] or ""))
    return _split_item_name_collisions(list(by_key.values()))


def _severity(points: list[dict]) -> int:
    """窗口内最近一次红/黄(红=0,黄=1),否则 2。"""
    for p in reversed(points):
        if p.get("color") in ("red", "yellow"):
            return 0 if p["color"] == "red" else 1
    return 2


def _has_abnormal(points: list[dict]) -> bool:
    """窗口内任一点红/黄。"""
    return any(p.get("color") in ("red", "yellow") for p in points)


def _points_range(points: list[dict]) -> float:
    """数值极差 max-min;经 _try_float 兼容 float(get_overview) 与 str(_series);
    无有效数值返回 0.0。"""
    vals = [v for v in (_try_float(p.get("value")) for p in points) if v is not None]
    return max(vals) - min(vals) if vals else 0.0


def _trend_sort_key(item: dict):
    """统一排序键:最近异常红>黄,同级按极差降序,再按指标名。"""
    pts = item["points"]
    return (_severity(pts), -_points_range(pts), item.get("item_name") or "")


def _endpoint_pct(points: list[dict]) -> Optional[float]:
    """最新点相对最旧点的 delta_pct。"""
    if len(points) < 2:
        return None
    pair = compute_delta(points[-1]["value"], points[0]["value"])
    return pair[1] if pair else None


def _rank_key_indicators(db: Session, window: list) -> list[dict]:
    """关键指标:出现在 ≥2 份窗口报告、且窗口内有过红/黄或首尾 |delta_pct|≥5。

    排序:最近异常点红 > 黄 > 无,同级按 |delta_pct| 降序,再按指标名。
    """
    ranked = []
    for item in _series(db, window):
        points = item["points"]
        if len({p["report_id"] for p in points}) < 2:
            continue
        pct = _endpoint_pct(points)
        sev = _severity(points)
        if sev >= 2 and (pct is None or abs(pct) < 5):
            continue
        ranked.append({
            "item_name": item["item_name"],
            "unit": item["unit"],
            "latest_value": points[-1]["value"],
            "latest_color": points[-1]["color"],
            "direction": trend_direction(points),
            "delta_pct": pct,
            "points": points,
            "_sev": sev,
            "_pct_abs": abs(pct) if pct is not None else 0.0,
        })
    ranked.sort(key=lambda x: (x["_sev"], -x["_pct_abs"], x["item_name"]))
    for x in ranked:
        x.pop("_sev", None)
        x.pop("_pct_abs", None)
    return ranked


def _parse_change_json(content: str) -> Optional[dict]:
    """宽容解析 MedGo 输出的 JSON 四键对象;失败返回 None。"""
    if not content:
        return None
    text0 = content.strip()
    if text0.startswith("```"):
        text0 = re.sub(r"^```[A-Za-z]*\n?", "", text0)
        text0 = re.sub(r"```$", "", text0).strip()
    try:
        obj = json.loads(text0)
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", text0, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except (TypeError, ValueError):
            return None
    if not isinstance(obj, dict):
        return None
    keys = ("trend_summary", "conclusion", "suggestions", "precautions")
    if not all(isinstance(obj.get(k), str) for k in keys):
        return None
    return {k: obj.get(k, "").strip() for k in keys}


def _call_llm_for_change_overview(prompt: str) -> Optional[dict]:
    """调 MedGo 生成总览。失败/解析失败返回 None 并记 warning。"""
    try:
        model = get_chat_model(streaming=False)
        resp = asyncio.run(_guarded(model.ainvoke([("user", prompt)], max_tokens=1024)))
        return _parse_change_json(strip_think_tags(resp.content or ""))
    except Exception as e:
        logger.warning("change overview LLM call failed: %s", e)
        return None


def get_change_overview(db: Session, user_id: str, name: str) -> dict:
    """GET /profile/change-overview 主入口。不足 2 份降级;否则读缓存或生成并写回。"""
    window = _change_window(db, user_id, name)
    if len(window) < 2:
        return empty_change_overview(len(window))
    newest_interp = window[-1][1]
    fingerprint = _window_std_fingerprint(db, [pair[0].id for pair in window])
    cached = _read_cached_overview(newest_interp, window, fingerprint)
    if cached:
        cached["cached"] = True
        return cached

    key_indicators = _rank_key_indicators(db, window)
    reports = [_report_header(pair) for pair in window]
    payload = {
        "reports": reports,
        "covered": len(reports),
        "key_indicators": key_indicators[:5],
        "summary": None,
        "cached": False,
    }
    prompt = build_change_prompt(reports, key_indicators)
    summary = _call_llm_for_change_overview(prompt)
    payload["summary"] = summary
    if summary:
        sig = [{"report_id": pair[0].id, "interp_id": pair[1].id} for pair in window]
        newest_interp.comparison_summary = json.dumps(
            {"signature": sig, "fingerprint": fingerprint, "payload": payload},
            ensure_ascii=False)
        newest_interp.comparison_baseline_id = None
        db.commit()
    return payload


def ensure_change_overview(db: Session, report_id: int) -> None:
    """worker 钩子:解读完成后按锚定重算窗口并预热缓存。任何异常吞掉不冒泡。"""
    try:
        report = db.query(ReportInfo).filter_by(id=report_id).first()
        if not report:
            return
        get_change_overview(db, report.user_id, report.name)
    except Exception as e:
        logger.warning("change overview pre-generation failed: %s", e)