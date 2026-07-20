from typing import Optional


def _try_float(s) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def match_indicators(current: list[dict], baseline: list[dict]) -> list[dict]:
    """按 item_name_standard 优先匹配,双边都空时 fallback item_name。

    单边有 standard 一边无 -> 跳过(避免误匹配)。
    单位不一致仍匹配,由 compute_delta 时判断是否可计算。
    """
    matches = []
    cur_by_std = {r.get("item_name_standard"): r for r in current if r.get("item_name_standard")}
    base_by_std = {r.get("item_name_standard"): r for r in baseline if r.get("item_name_standard")}
    for std, c in cur_by_std.items():
        b = base_by_std.get(std)
        if b:
            matches.append({
                "item_name_standard": std,
                "item_name": c.get("item_name", std),
                "current_value": c.get("result_value"),
                "baseline_value": b.get("result_value"),
                "unit": c.get("unit"),
                "current_color": c.get("color_level"),
                "baseline_color": b.get("color_level"),
            })
    cur_no_std = [r for r in current if not r.get("item_name_standard")]
    base_no_std_by_name = {r.get("item_name"): r for r in baseline if not r.get("item_name_standard")}
    for c in cur_no_std:
        b = base_no_std_by_name.get(c.get("item_name"))
        if b:
            matches.append({
                "item_name_standard": None,
                "item_name": c.get("item_name"),
                "current_value": c.get("result_value"),
                "baseline_value": b.get("result_value"),
                "unit": c.get("unit"),
                "current_color": c.get("color_level"),
                "baseline_color": b.get("color_level"),
            })
    return matches


def compute_delta(current_value: str, baseline_value: str) -> Optional[tuple[float, float]]:
    """返回 (delta, delta_pct)。非数值返回 None。

    delta_pct = (current - baseline) / |baseline| * 100,baseline=0 时返回 None。
    """
    c = _try_float(current_value)
    b = _try_float(baseline_value)
    if c is None or b is None:
        return None
    if b == 0:
        return None
    delta = round(c - b, 4)
    delta_pct = round((c - b) / abs(b) * 100, 2)
    return delta, delta_pct


def judge_status(delta_pct: float) -> str:
    if delta_pct <= -5:
        return "improved"
    if delta_pct >= 5:
        return "worsened"
    return "stable"


def trend_direction(points: list[dict]) -> Optional[str]:
    if len(points) < 2:
        return None
    last = _try_float(points[-1].get("value"))
    prev = _try_float(points[-2].get("value"))
    if last is None or prev is None:
        return None
    if last > prev:
        return "up"
    if last < prev:
        return "down"
    return None


def build_comparison_prompt(current_report: dict, baseline_report: dict,
                            indicators_diff: list[dict], top_abnormal: list[dict]) -> str:
    """拼出给 MedGo 的中文 prompt。indicators_diff 与 top_abnormal 在 worker 钩子里通常是同一份数据。"""
    cur_level = current_report.get("overall_level") or "未知"
    base_level = baseline_report.get("overall_level") or "未知"

    abnormal_lines = []
    for ind in top_abnormal[:5]:
        name = ind.get("item_name") or ind.get("item_name_standard") or ""
        cur_v = ind.get("current_value", "")
        unit = ind.get("unit", "")
        cur_color = ind.get("current_color") or ""
        base_v = ind.get("baseline_value", "")
        delta = ind.get("delta")
        arrow = ""
        if delta is not None:
            arrow = f",上次{base_v}," + ("↑" if delta > 0 else "↓") + f"{abs(delta)}"
        abnormal_lines.append(
            f"  - {name}:{cur_v} {unit}({cur_color or '未判色'}{arrow})"
        )
    abnormal_text = "\n".join(abnormal_lines) or "  (无异常指标)"

    return f"""你是体检报告解读助手。基于下方两份报告的对比数据,用通俗易懂的中文写一段健康变化小结(150-250字)。

## 本次报告({current_report.get('report_date', '未知')})
- 总体:{cur_level} | 红区{current_report.get('red_count', 0)} 黄区{current_report.get('yellow_count', 0)} 绿区{current_report.get('green_count', 0)}
- 异常指标:
{abnormal_text}

## 上一份报告({baseline_report.get('report_date', '未知')})
- 总体:{base_level} | 红区{baseline_report.get('red_count', 0)} 黄区{baseline_report.get('yellow_count', 0)} 绿区{baseline_report.get('green_count', 0)}

## 小结要求
1. 先说整体变化(红黄区数量变化、新增/消失的异常)
2. 再点出明显改善和明显恶化的指标
3. 给出 1-2 条针对性建议(基于上述指标,不编造)
4. 不下诊断,语气同解读模块
5. 不输出 thinking 标签
"""