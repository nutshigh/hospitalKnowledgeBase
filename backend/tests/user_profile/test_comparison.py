"""comparison.py 纯函数单测。无 DB 依赖。"""
import pytest

from app.modules.user_profile.comparison import (
    compute_delta,
    trend_direction,
    build_change_prompt,
)


def test_compute_delta_numeric():
    assert compute_delta("6.8", "7.2") == (-0.4, pytest.approx(-5.56, rel=1e-2))


def test_compute_delta_non_numeric_returns_none():
    assert compute_delta("阳性", "阴性") is None
    assert compute_delta("++", "+") is None


def test_compute_delta_missing_value_returns_none():
    assert compute_delta("", "7.2") is None
    assert compute_delta("6.8", None) is None


def test_trend_direction_up():
    points = [{"value": 5.0}, {"value": 6.5}]
    assert trend_direction(points) == "up"


def test_trend_direction_down():
    points = [{"value": 7.0}, {"value": 6.0}]
    assert trend_direction(points) == "down"


def test_trend_direction_single_point_is_none():
    assert trend_direction([{"value": 6.0}]) is None
    assert trend_direction([]) is None


def test_build_change_prompt_contains_window_and_sections():
    reports = [
        {"report_date": "2024-05-01", "overall_level": "green",
         "red_count": 0, "yellow_count": 1, "green_count": 10},
        {"report_date": "2025-05-01", "overall_level": "yellow",
         "red_count": 1, "yellow_count": 2, "green_count": 9},
    ]
    key_indicators = [
        {"item_name": "空腹血糖", "unit": "mmol/L", "delta_pct": -11.1,
         "points": [
             {"report_date": "2024-05-01", "value": "7.2", "color": "red"},
             {"report_date": "2025-05-01", "value": "6.4", "color": "green"},
         ]},
    ]
    prompt = build_change_prompt(reports, key_indicators)
    assert "2024-05-01" in prompt and "2025-05-01" in prompt
    assert "空腹血糖" in prompt
    assert "trend_summary" in prompt and "precautions" in prompt
    assert "conclusion" in prompt and "suggestions" in prompt