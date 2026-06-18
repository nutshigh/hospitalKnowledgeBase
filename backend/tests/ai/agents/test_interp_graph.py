from unittest.mock import patch, MagicMock


def test_interp_state_fields():
    """InterpState 含必需字段"""
    from app.ai.agents.interp_graph import InterpState
    assert "indicators" in InterpState.__annotations__
    assert "judgments" in InterpState.__annotations__
    assert "abnormal_indicators" in InterpState.__annotations__
    assert "agent_explanations" in InterpState.__annotations__
    assert "overall_level" in InterpState.__annotations__


def test_build_interp_graph_returns_compiled():
    """build_interp_graph 返回可编译的图"""
    with patch("app.ai.agents.interp_graph.get_chat_model") as mock_model, \
         patch("app.ai.agents.interp_graph.make_tools") as mock_tools:
        mock_model.return_value = MagicMock()
        mock_model.return_value.bind_tools.return_value = MagicMock()
        mock_tools.return_value = []

        from app.ai.agents.interp_graph import build_interp_graph
        graph = build_interp_graph("H001", MagicMock())
        assert graph is not None
