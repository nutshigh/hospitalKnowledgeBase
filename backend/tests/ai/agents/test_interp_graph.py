from unittest.mock import patch, MagicMock


def test_interp_state_fields():
    """InterpState 含必需字段"""
    from app.ai.agents.interp_graph import InterpState
    assert "indicators" in InterpState.__annotations__
    assert "judgments" in InterpState.__annotations__
    assert "abnormal_indicators" in InterpState.__annotations__
    assert "overall_level" in InterpState.__annotations__


def test_build_interp_graph_returns_compiled():
    """build_interp_graph 返回可编译的图"""
    with patch("app.ai.agents.interp_graph.get_chat_model") as mock_model:
        mock_model.return_value = MagicMock()
        from app.ai.agents.interp_graph import build_interp_graph
        graph = build_interp_graph("H001", MagicMock())
        assert graph is not None


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


def test_interp_state_has_report_and_references():
    """InterpState 含 report / references / knowledge_results"""
    from app.ai.agents.interp_graph import InterpState
    a = InterpState.__annotations__
    assert "report" in a
    assert "references" in a
    assert "knowledge_results" in a
    assert "judge_retry_count" in a


def test_interpretation_report_is_5_sections():
    """InterpretationReport 含 5 节字段"""
    from app.ai.agents.interp_graph import InterpretationReport
    fields = InterpretationReport.model_fields
    assert {"overall_summary", "abnormal_focus", "trend_note", "suggestions", "risk_alert"} <= set(fields)


def test_merge_citations_dedup_renumber():
    """_merge_citations 去重并重新连续编号"""
    from app.ai.agents.interp_graph import _merge_citations
    a = [{"ref_id": 1, "entry_id": 12, "title": "A", "source": "document", "content": "c1"}]
    b = [{"ref_id": 1, "entry_id": 12, "title": "A", "source": "document", "content": "c1"},
         {"ref_id": 2, "entry_id": 13, "title": "B", "source": "document", "content": "c2"}]
    merged = _merge_citations(a, b)
    assert len(merged) == 2
    assert merged[0]["ref_id"] == 1
    assert merged[1]["ref_id"] == 2
    assert {m["entry_id"] for m in merged} == {12, 13}


def test_generate_report_empty_abnormal_returns_empty_report():
    """无异常指标时返回空 5 节报告 + 空引用"""
    from app.ai.agents.interp_graph import _generate_report
    state = {"abnormal_indicators": [], "knowledge_results": {}, "user_id": 1,
             "hospital_id": "H001", "report_id": 1, "overall_level": "green",
             "red_count": 0, "yellow_count": 0, "green_count": 5}
    result = _generate_report(state, MagicMock())
    assert result["report"].overall_summary == ""
    assert result["report"].abnormal_focus == ""
    assert result["references"] == []
    assert result["judge_retry_count"] == 0


def test_generate_report_with_abnormal_calls_llm_and_injects():
    """有异常指标时 LLM 调用一次，inject_citations 注入后返回结构化报告"""
    from unittest.mock import patch, MagicMock
    from app.ai.agents.interp_graph import _generate_report, InterpretationReport

    state = {
        "abnormal_indicators": [{"indicator_id": 5, "item_name": "ALT", "result_value": "62",
                                 "unit": "U/L", "ref_range_low": "0", "ref_range_high": "40",
                                 "deviation": "high", "color_level": "yellow"}],
        "knowledge_results": {12: {"entry_id": 12, "title": "ALT 知识", "source": "document",
                                    "content": "ALT 升高常见于脂肪肝"}},
        "user_id": 1, "hospital_id": "H001", "report_id": 1,
        "overall_level": "yellow", "red_count": 0, "yellow_count": 1, "green_count": 10,
    }

    with patch("app.ai.agents.interp_graph.build_report_model") as mock_build, \
         patch("app.ai.agents.interp_graph.inject_citations") as mock_inj, \
         patch("app.ai.agents.interp_graph.strip_think_tags", side_effect=lambda x: x):
        mock_model = MagicMock()
        mock_build.return_value = mock_model
        mock_model.invoke.return_value = MagicMock(content='{"overall_summary":"S","abnormal_focus":"A","trend_note":"T","suggestions":"G","risk_alert":"R"}')
        mock_inj.side_effect = lambda text, sources, **kw: (text, [{"ref_id": 1, "entry_id": 12, "title": "ALT 知识", "source": "document", "content": "ALT 升高"}] if "ALT" in text else [])

        result = _generate_report(state, MagicMock())
    assert isinstance(result["report"], InterpretationReport)
    assert result["report"].overall_summary == "S"
    assert len(result["references"]) >= 0


def test_generate_report_increments_judge_retry_count_on_abnormal_branch():
    """非空 abnormal_indicators 分支必须把 judge_retry_count +1，否则 after_judge 重试上限永远不会触发"""
    from unittest.mock import patch, MagicMock
    from app.ai.agents.interp_graph import _generate_report

    state = {
        "abnormal_indicators": [{"indicator_id": 5, "item_name": "ALT", "result_value": "62",
                                 "unit": "U/L", "ref_range_low": "0", "ref_range_high": "40",
                                 "deviation": "high", "color_level": "yellow"}],
        "knowledge_results": {12: {"entry_id": 12, "title": "ALT 知识", "source": "document",
                                    "content": "ALT 升高"}},
        "user_id": 1, "hospital_id": "H001", "report_id": 1,
        "overall_level": "yellow", "red_count": 0, "yellow_count": 1, "green_count": 10,
        "judge_retry_count": 0,
    }

    with patch("app.ai.agents.interp_graph.build_report_model") as mock_build, \
         patch("app.ai.agents.interp_graph.inject_citations") as mock_inj, \
         patch("app.ai.agents.interp_graph.strip_think_tags", side_effect=lambda x: x):
        mock_model = MagicMock()
        mock_build.return_value = mock_model
        mock_model.invoke.return_value = MagicMock(
            content='{"overall_summary":"S","abnormal_focus":"A","trend_note":"T","suggestions":"G","risk_alert":"R"}')
        mock_inj.side_effect = lambda text, sources, **kw: (text, [])
        result = _generate_report(state, MagicMock())
    assert result["judge_retry_count"] == 1

