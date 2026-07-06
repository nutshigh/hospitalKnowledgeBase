from unittest.mock import patch, MagicMock

from pydantic import BaseModel


def test_interp_state_fields():
    """InterpState 含必需字段"""
    from app.ai.agents.interp_graph import InterpState
    assert "indicators" in InterpState.__annotations__
    assert "judgments" in InterpState.__annotations__
    assert "abnormal_indicators" in InterpState.__annotations__
    assert "agent_explanations" in InterpState.__annotations__
    assert "overall_level" in InterpState.__annotations__


def test_interp_batch_result_is_pydantic():
    """InterpBatchResult 是 Pydantic BaseModel（非 TypedDict）"""
    from app.ai.agents.interp_graph import InterpBatchResult
    assert issubclass(InterpBatchResult, BaseModel)
    inst = InterpBatchResult(items=[])
    assert inst.items == []


def test_build_interp_graph_returns_compiled():
    """build_interp_graph 返回可编译的图"""
    with patch("app.ai.agents.interp_graph.get_chat_model") as mock_model:
        mock_model.return_value = MagicMock()
        from app.ai.agents.interp_graph import build_interp_graph
        graph = build_interp_graph("H001", MagicMock())
        assert graph is not None


def test_build_interp_agent_returns_compiled():
    """build_interp_agent 返回非 None（create_agent 产物）"""
    with patch("app.ai.agents.interp_graph.get_chat_model") as mock_model:
        mock_model.return_value = MagicMock()
        from app.ai.agents.interp_graph import build_interp_agent
        agent = build_interp_agent()
        assert agent is not None


def test_agent_batch_empty_abnormal_returns_empty():
    """abnormal_indicators 为空时 agent_batch 返回空结果"""
    from app.ai.agents.interp_graph import _agent_batch
    result = _agent_batch(
        {"abnormal_indicators": [], "hospital_id": "H001", "report_id": 1},
        MagicMock(),
        MagicMock(),
    )
    assert result["agent_explanations"] == {}
    assert result["knowledge_refs"] == {}
    assert result["judge_retry_count"] == 0


def test_map_structured_to_explanations():
    """_map_structured_to_explanations 正确映射结构化输出到 explanations/refs"""
    from app.ai.agents.interp_graph import (
        _map_structured_to_explanations, InterpBatchItem, InterpBatchResult, Citation,
    )
    from unittest.mock import patch

    structured = InterpBatchResult(items=[
        InterpBatchItem(indicator_id=10, explanation="解读A", suggestion="建议A",
                        certainty="definite", certainty_reason="数值对比",
                        citations=[Citation(ref_id=1, entry_id=101, title="知识A", source="document")]),
        InterpBatchItem(indicator_id=20, explanation="解读B", suggestion="建议B",
                        certainty="probable", certainty_reason="推理",
                        citations=[Citation(ref_id=1, entry_id=201, title="知识B1", source="document"),
                                   Citation(ref_id=2, entry_id=202, title="知识B2", source="document")]),
    ])
    knowledge_results = {
        101: {"entry_id": 101, "title": "知识A", "source": "document", "content": "解读A相关内容"},
        201: {"entry_id": 201, "title": "知识B1", "source": "document", "content": "解读B相关"},
        202: {"entry_id": 202, "title": "知识B2", "source": "document", "content": "建议B相关"},
    }
    abnormal = [{"indicator_id": 10}, {"indicator_id": 20}, {"indicator_id": 30}]

    # inject_citations 调用 embedding 服务，mock 为返回空（跳过标注）
    with patch("app.ai.agents.citation_matcher.inject_citations") as mock_inject:
        mock_inject.side_effect = lambda text, sources, **kw: (text, [])
        explanations, mapped_refs = _map_structured_to_explanations(structured, knowledge_results, abnormal)

    assert explanations[10]["explanation"] == "解读A"
    assert explanations[10]["suggestion"] == "建议A"
    assert explanations[10]["certainty"] == "definite"
    assert explanations[20]["certainty"] == "probable"
    # 30 未在结构化输出，补全为空 + refused
    assert explanations[30]["explanation"] == ""
    assert explanations[30]["certainty"] == "refused"
    # refs 来自 inject_citations 返回（mock 返回空）
    assert mapped_refs[10] == []
    # 30 fallback 用全部 knowledge_results
    assert len(mapped_refs[30]) == 3


def test_interp_knowledge_middleware_extracts_refs_dict():
    """InterpKnowledgeMiddleware 从 search_knowledge 结果提取 {entry_id: ref}"""
    import json
    from langchain.messages import ToolMessage
    from langgraph.types import Command
    from app.ai.agents.interp_graph import InterpKnowledgeMiddleware

    mw = InterpKnowledgeMiddleware()
    tool_msg = ToolMessage(
        content=json.dumps([{"entry_id": 101, "title": "知识A", "content": "...", "score": 0.9, "source": "document"}]),
        tool_call_id="call_1",
    )
    request = MagicMock()
    request.tool_call = {"name": "search_knowledge", "id": "call_1", "args": {}}
    handler = MagicMock(return_value=tool_msg)

    result = mw.wrap_tool_call(request, handler)
    assert isinstance(result, Command)
    kr = result.update["knowledge_results"]
    assert 101 in kr
    assert kr[101]["entry_id"] == 101
    assert kr[101]["title"] == "知识A"
    assert kr[101]["source"] == "document"


def test_report_interpretation_has_summary_refs_and_quality_note():
    """ReportInterpretation 模型含 summary_refs / quality_note 字段"""
    from app.modules.interpretation.models import ReportInterpretation
    cols = {c.name for c in ReportInterpretation.__table__.columns}
    assert "summary_refs" in cols
    assert "quality_note" in cols


def test_interpretation_response_schema_fields():
    """新 InterpretationResponse 含 summaries/references/quality_note，无 summary_text"""
    from app.modules.interpretation.schemas import InterpretationResponse
    fields = InterpretationResponse.model_fields
    assert "summaries" in fields
    assert "references" in fields
    assert "quality_note" in fields
    assert "summary_text" not in fields


def test_indicator_judgment_schema_no_explanation():
    """IndicatorJudgmentSchema 不再含 explanation/suggestion，含 unit/ref_range"""
    from app.modules.interpretation.schemas import IndicatorJudgmentSchema
    fields = IndicatorJudgmentSchema.model_fields
    assert "explanation" not in fields
    assert "suggestion" not in fields
    assert "unit" in fields
    assert "ref_range_low" in fields
    assert "ref_range_high" in fields


def test_parse_summary_text_roundtrip():
    """parse_summary_text 把 5 节 JSON 解析回 schema，空输入返回空 5 节"""
    from app.modules.interpretation.schemas import parse_summary_text, InterpretationReportSchema
    raw = {
        "overall_summary": "整体评估", "abnormal_focus": "异常解读",
        "trend_note": "趋势", "suggestions": "建议", "risk_alert": "风险"
    }
    parsed = parse_summary_text(__import__("json").dumps(raw))
    assert isinstance(parsed, InterpretationReportSchema)
    assert parsed.overall_summary == "整体评估"
    empty = parse_summary_text(None)
    assert empty.overall_summary == ""


def test_judge_review_format_for_comprehensive_report():
    """_format_for_review 输出综合报告 5 节 + 异常指标 + 引用文本"""
    from app.ai.agents.judge_graph import _format_for_review
    from app.ai.agents.interp_graph import InterpretationReport
    state = {
        "report": InterpretationReport(
            overall_summary="整体评估内容", abnormal_focus="ALT 升高 [1]",
            trend_note="", suggestions="建议戒酒", risk_alert="",
        ),
        "references": [{"ref_id": 1, "entry_id": 12, "title": "ALT 知识", "source": "document"}],
        "abnormal_indicators": [{"indicator_id": 5, "item_name": "ALT", "result_value": "62",
                                  "unit": "U/L", "deviation": "high", "color_level": "yellow"}],
    }
    text = _format_for_review(state)
    assert "整体评估内容" in text
    assert "ALT 升高" in text
    assert "ALT" in text
    assert "62" in text
    assert "[1]" in text
    assert "ALT 知识" in text


def test_run_judge_passes_when_agent_passthrough():
    """Judge agent 抛异常时 run_judge 返回 passed=True，不阻塞"""
    from unittest.mock import patch
    from app.ai.agents.judge_graph import run_judge
    from app.ai.agents.interp_graph import InterpretationReport
    state = {
        "report": InterpretationReport(overall_summary="x", abnormal_focus="x",
                                        trend_note="", suggestions="", risk_alert=""),
        "references": [],
        "abnormal_indicators": [],
    }
    with patch("app.ai.agents.judge_graph.build_judge_agent") as mock_b:
        mock_b.side_effect = RuntimeError("boom")
        result = run_judge(state)
    assert result["passed"] is True


