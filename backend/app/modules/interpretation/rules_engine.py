from typing import List, Optional, Dict
from dataclasses import dataclass


@dataclass
class RuleResult:
    color_level: str
    deviation: str
    matched_rule_id: Optional[int] = None
    matched_rule_name: str = ""


class RulesEngine:
    def __init__(self):
        self._rules_cache: Dict[str, List[dict]] = {}

    def load_rules(self, hospital_id: str, rules: List[dict]):
        self._rules_cache[hospital_id] = sorted(rules, key=lambda r: r.get("priority", 0))

    def evaluate(self, hospital_id: str, indicator: dict, history: List[dict] = None) -> RuleResult:
        rules = self._rules_cache.get(hospital_id, [])
        rules = [r for r in rules if r.get("is_active", 1)]
        result = RuleResult(color_level="green", deviation="normal")

        for rule in rules:
            rule_type = rule.get("rule_type")
            conditions = rule.get("conditions", {})

            if rule_type == "value_range" and self._match_value_range(indicator, conditions):
                result = self._upgrade(result, rule, conditions)
            elif rule_type == "key_indicator" and self._match_key_indicator(indicator, rule):
                result = self._upgrade(result, rule, conditions)
            elif rule_type == "trend" and history and self._match_trend(indicator, history, conditions):
                result = self._upgrade(result, rule, conditions)

        return result

    def _match_value_range(self, indicator: dict, conditions: dict) -> bool:
        try:
            value = float(indicator.get("result_value", 0))
        except (ValueError, TypeError):
            return False

        multiplier = float(conditions.get("multiplier", 1))
        ref_high = indicator.get("ref_range_high")
        ref_low = indicator.get("ref_range_low")

        if ref_high and ref_low:
            try:
                if value > float(ref_high) * multiplier:
                    return True
                if value < float(ref_low) * multiplier:
                    return True
            except (ValueError, TypeError):
                pass

        op = conditions.get("op", "")
        threshold = float(conditions.get("value", 0))
        if op == "gt" and value > threshold:
            return True
        if op == "gte" and value >= threshold:
            return True
        if op == "lt" and value < threshold:
            return True
        return False

    def _match_key_indicator(self, indicator: dict, rule: dict) -> bool:
        indicator_code = rule.get("indicator_code", "").strip()
        if not indicator_code:
            return False
        item_name = indicator.get("item_name", "")
        item_standard = indicator.get("item_name_standard", "") or ""
        try:
            value = float(indicator.get("result_value", 0))
        except (ValueError, TypeError):
            return False
        return (indicator_code in item_name or indicator_code in item_standard) and value != 0

    def _match_trend(self, indicator: dict, history: List[dict], conditions: dict) -> bool:
        if len(history) < 2:
            return False
        values = []
        for h in history:
            try:
                values.append(float(h.get("result_value", 0)))
            except (ValueError, TypeError):
                continue
        if len(values) < 2:
            return False
        ref_high = indicator.get("ref_range_high")
        if ref_high:
            try:
                ref = float(ref_high)
                return all(v > ref for v in values) and values[-1] > values[0]
            except (ValueError, TypeError):
                pass
        return False

    def _upgrade(self, current: RuleResult, rule: dict, conditions: dict) -> RuleResult:
        new_level = rule.get("color_level", current.color_level)
        if _level_rank(new_level) > _level_rank(current.color_level):
            deviation = "critical" if conditions.get("critical", False) else _infer_deviation(conditions, current.deviation)
            return RuleResult(
                color_level=new_level, deviation=deviation,
                matched_rule_id=rule.get("id"), matched_rule_name=rule.get("rule_name", ""),
            )
        return current

    def evaluate_report(self, hospital_id: str, indicators: List[dict],
                        history_map: Dict[str, List[dict]] = None) -> List[RuleResult]:
        history_map = history_map or {}
        results = []
        for ind in indicators:
            item_key = ind.get("item_name_standard") or ind.get("item_name", "")
            history = history_map.get(item_key, [])
            results.append(self.evaluate(hospital_id, ind, history))
        return results


def _level_rank(level: str) -> int:
    return {"green": 0, "yellow": 1, "red": 2}.get(level, 0)


def _infer_deviation(conditions: dict, fallback: str) -> str:
    op = conditions.get("op", "")
    if op in ("gt", "gte"):
        return "high"
    if op in ("lt", "lte"):
        return "low"
    return fallback


rules_engine = RulesEngine()
