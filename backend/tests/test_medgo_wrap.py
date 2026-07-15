"""验证所有 MedGo 调用点都被 medgo_sem 收口。
通过 monkeypatch ChatOpenAI.{invoke,ainvoke,astream} 计数,统计 acquire 期间的活动。
此测试不启动真 vLLM;只验证 wrap 是否存在。"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


def test_report_parse_text_uses_sem(monkeypatch):
    """report/service._parse_text_with_llm 调用必须经过 sem。"""
    import app.ai.llm as llm_mod
    calls = {"acquired": 0}

    orig_sem = llm_mod.medgo_sem

    class TrackingSem:
        async def __aenter__(self):
            calls["acquired"] += 1
            return self

        async def __aexit__(self, *a):
            pass

    llm_mod.medgo_sem = TrackingSem()
    try:
        canned = type(
            "R", (),
            {"content": '{"name":null,"gender":null,"age":null,"report_date":null,"indicators":[]}'},
        )()
        with patch("app.ai.llm.ChatOpenAI") as M:
            M.return_value.invoke.return_value = canned
            M.return_value.ainvoke = AsyncMock(return_value=canned)
            from app.modules.report.service import _parse_text_with_llm
            _parse_text_with_llm("some text")
        assert calls["acquired"] >= 1, "MedGo 调用未经过 medgo_sem"
    finally:
        llm_mod.medgo_sem = orig_sem


@pytest.mark.asyncio
async def test_chat_planner_uses_sem(monkeypatch):
    """chat_planner.run_planner 的 MedGo 调用必须经过 sem。"""
    import app.ai.llm as llm_mod
    calls = {"acquired": 0}

    orig_sem = llm_mod.medgo_sem

    class TrackingSem:
        async def __aenter__(self):
            calls["acquired"] += 1
            return self

        async def __aexit__(self, *a):
            pass

    llm_mod.medgo_sem = TrackingSem()
    try:
        from app.ai.agents.chat_planner import ChatPlan
        fake_model = MagicMock()
        fake_model.max_tokens = 4096
        fake_model.temperature = 0.1
        fake_model.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=ChatPlan(need_tools=False, tool_calls=[])
        )
        with patch("app.ai.agents.chat_planner.get_chat_model", return_value=fake_model):
            from app.ai.agents.chat_planner import run_planner
            await run_planner("H001", [], "你好", None, 4)
        assert calls["acquired"] >= 1, "planner MedGo 调用未经过 medgo_sem"
    finally:
        llm_mod.medgo_sem = orig_sem