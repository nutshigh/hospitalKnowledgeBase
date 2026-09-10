"""工具调用守卫单测(2026-09-10): 截断 JSON 丢弃、超长 query 截断、超量限流、合法原样。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.agents.interp_graph import (
    _guard_sanitize_tool_calls, _GUARD_MAX_QUERY, _GUARD_MAX_CALLS,
)


def test_broken_json_dropped():
    bad = {"name": "search_knowledge", "args": "{'query': '截断", "id": "b", "type": "tool_call"}
    keep, dropped = _guard_sanitize_tool_calls([bad])
    assert keep == [] and dropped == 1


def test_long_query_truncated_kept():
    long_q = {"name": "search_knowledge", "args": {"query": "x" * 2000}, "id": "c", "type": "tool_call"}
    keep, dropped = _guard_sanitize_tool_calls([long_q])
    assert dropped == 0
    assert len(keep[0]["args"]["query"]) == _GUARD_MAX_QUERY


def test_too_many_calls_capped():
    many = [{"name": "search_knowledge", "args": {"query": str(i)}, "id": str(i), "type": "tool_call"}
            for i in range(_GUARD_MAX_CALLS + 8)]
    keep, dropped = _guard_sanitize_tool_calls(many)
    assert len(keep) == _GUARD_MAX_CALLS and dropped == 8


def test_valid_call_unchanged():
    ok = {"name": "search_knowledge", "args": {"query": "糖尿病"}, "id": "d", "type": "tool_call"}
    keep, dropped = _guard_sanitize_tool_calls([ok])
    assert keep == [ok] and dropped == 0
