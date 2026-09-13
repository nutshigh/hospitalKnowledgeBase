"""tool-call 守卫单测(2026-09-12): 截断 JSON 被 langchain 归档 invalid_tool_calls
时(tool_calls 为空), 旧守卫直接跳过导致坏串回传 vLLM 400(H003-22 实例)。
"""
from langchain_core.messages import AIMessage

from app.ai.agents.interp_graph import _guard_sanitize_message, _GUARD_MAX_QUERY

_BAD_ARGS = '[{"name": "search_knowledge", "args": {"query": "研究分析研究分析'


def _msg(tool_calls=None, invalid_tool_calls=None, raw_tool_calls=None):
    akw = {}
    if raw_tool_calls is not None:
        akw["tool_calls"] = raw_tool_calls
    return AIMessage(content="", tool_calls=tool_calls or [],
                     invalid_tool_calls=invalid_tool_calls or [],
                     additional_kwargs=akw)


def test_invalid_tool_calls_with_empty_tool_calls_are_cleared():
    """核心回归: tool_calls 为空 + invalid_tool_calls 非空 → 必须清洗。"""
    m = _msg(
        tool_calls=[],
        invalid_tool_calls=[{"name": "search_knowledge", "args": _BAD_ARGS,
                             "id": "call_1", "error": None, "type": "invalid_tool_call"}],
        raw_tool_calls=[{"id": "call_1", "type": "function",
                         "function": {"name": "search_knowledge", "arguments": _BAD_ARGS}}],
    )
    out, dropped = _guard_sanitize_message(m)
    assert dropped == 1
    assert out.invalid_tool_calls == []
    assert "tool_calls" not in (out.additional_kwargs or {})


def test_raw_bad_arguments_cleared_even_without_invalid_list():
    m = _msg(raw_tool_calls=[{"id": "c1", "type": "function",
                              "function": {"name": "search_knowledge", "arguments": _BAD_ARGS}}])
    out, dropped = _guard_sanitize_message(m)
    assert "tool_calls" not in (out.additional_kwargs or {})


def test_valid_message_untouched():
    good = {"name": "search_knowledge", "args": {"query": "糖尿病"},
            "id": "c1", "type": "tool_call"}
    raw = [{"id": "c1", "type": "function",
            "function": {"name": "search_knowledge", "arguments": '{"query": "糖尿病"}'}}]
    m = _msg(tool_calls=[good], raw_tool_calls=raw)
    out, dropped = _guard_sanitize_message(m)
    assert dropped == 0 and out is m


def test_overlong_query_truncated():
    long_q = "高" * (_GUARD_MAX_QUERY + 200)
    m = _msg(tool_calls=[{"name": "search_knowledge", "args": {"query": long_q},
                          "id": "c1", "type": "tool_call"}])
    out, dropped = _guard_sanitize_message(m)
    assert dropped == 0
    assert len(out.tool_calls[0]["args"]["query"]) == _GUARD_MAX_QUERY
