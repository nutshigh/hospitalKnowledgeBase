"""comparison.py 纯函数单测。无 DB 依赖。"""
import pytest

from app.modules.user_profile.comparison import (
    match_indicators,
    compute_delta,
    judge_status,
    trend_direction,
    build_comparison_prompt,
)


def test_match_indicators_by_standard():
    """item_name_standard 一致但原名不同,仍应匹配"""
    current = [
        {"item_name": "血糖", "item_name_standard": "空腹血糖", "result_value": "6.8",
         "unit": "mmol/L", "color_level": "red"},
    ]
    baseline = [
        {"item_name": "GLU", "item_name_standard": "空腹血糖", "result_value": "7.2",
         "unit": "mmol/L", "color_level": "red"},
    ]
    matches = match_indicators(current, baseline)
    assert len(matches) == 1
    assert matches[0]["item_name_standard"] == "空腹血糖"
    assert matches[0]["current_value"] == "6.8"
    assert matches[0]["baseline_value"] == "7.2"
    assert matches[0]["unit"] == "mmol/L"


def test_match_indicators_fallback_to_item_name_when_standard_missing():
    """双边 item_name_standard 都空,fallback item_name 严格匹配"""
    current = [
        {"item_name": "血压", "item_name_standard": None, "result_value": "145",
         "unit": "mmHg", "color_level": "red"},
    ]
    baseline = [
        {"item_name": "血压", "item_name_standard": None, "result_value": "130",
         "unit": "mmHg", "color_level": "green"},
    ]
    matches = match_indicators(current, baseline)
    assert len(matches) == 1
    assert matches[0]["current_value"] == "145"
    assert matches[0]["baseline_value"] == "130"


def test_match_indicators_only_baseline_standard_keeps_item_name_match():
    """一边有 standard 一边无 -> 不匹配(不强行 fallback)"""
    current = [
        {"item_name": "血糖", "item_name_standard": "空腹血糖", "result_value": "6.8",
         "unit": "mmol/L", "color_level": "red"},
    ]
    baseline = [
        {"item_name": "血糖", "item_name_standard": None, "result_value": "7.2",
         "unit": "mmol/L", "color_level": "red"},
    ]
    matches = match_indicators(current, baseline)
    assert len(matches) == 0


def test_compute_delta_numeric():
    assert compute_delta("6.8", "7.2") == (-0.4, pytest.approx(-5.56, rel=1e-2))


def test_compute_delta_non_numeric_returns_none():
    assert compute_delta("阳性", "阴性") is None
    assert compute_delta("++", "+") is None


def test_compute_delta_missing_value_returns_none():
    assert compute_delta("", "7.2") is None
    assert compute_delta("6.8", None) is None


def test_judge_status_thresholds():
    assert judge_status(-10) == "improved"
    assert judge_status(-5) == "improved"
    assert judge_status(-4.9) == "stable"
    assert judge_status(4.9) == "stable"
    assert judge_status(5) == "worsened"
    assert judge_status(15) == "worsened"


def test_trend_direction_up():
    points = [{"value": 5.0}, {"value": 6.5}]
    assert trend_direction(points) == "up"


def test_trend_direction_down():
    points = [{"value": 7.0}, {"value": 6.0}]
    assert trend_direction(points) == "down"


def test_trend_direction_single_point_is_none():
    assert trend_direction([{"value": 6.0}]) is None
    assert trend_direction([]) is None


def test_build_comparison_prompt_contains_key_sections():
    current_report = {"report_date": "2026-06-15", "overall_level": "yellow",
                      "red_count": 3, "yellow_count": 5, "green_count": 12}
    baseline_report = {"report_date": "2025-11-02", "overall_level": "red",
                       "red_count": 5, "yellow_count": 3, "green_count": 10}
    indicators_diff = [
        {"item_name": "血糖", "current_value": "6.8", "baseline_value": "7.2",
         "unit": "mmol/L", "current_color": "red", "delta": -0.4},
        {"item_name": "收缩压", "current_value": "145", "baseline_value": "130",
         "unit": "mmHg", "current_color": "red", "delta": 15},
    ]
    prompt = build_comparison_prompt(current_report, baseline_report, indicators_diff, indicators_diff)
    assert "2026-06-15" in prompt
    assert "2025-11-02" in prompt
    assert "血糖" in prompt
    assert "收缩压" in prompt
    assert "红区" in prompt
    assert "建议" in prompt