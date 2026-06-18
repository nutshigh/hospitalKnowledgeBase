"""集成测试：验证 ai 层与 modules 层的端到端衔接（mock LLM/Milvus/RabbitMQ）。"""
from unittest.mock import patch, MagicMock


def test_knowledge_crud_to_rag_pipeline():
    """create_entry → ai.rag.index_documents → search → ai.rag.search 全链路"""
    with patch("app.ai.rag.RAGIndexer") as MockIndexer, \
         patch("app.ai.rag.RAGRetriever") as MockRetriever:
        mock_idx = MagicMock()
        MockIndexer.return_value = mock_idx
        mock_idx.index_documents.return_value = ["n1"]

        mock_ret = MagicMock()
        MockRetriever.return_value = mock_ret
        from app.modules.knowledge.schemas import SearchResult
        mock_ret.retrieve.return_value = [SearchResult(
            entry_id=1, title="血糖知识", content="空腹血糖正常值3.9-6.1",
            category_id=2, score=0.95
        )]

        from app.ai.rag import index_documents, search
        ids = index_documents("H001", [], 2, "test.pdf")
        assert ids == ["n1"]

        results = search("H001", "空腹血糖")
        assert results[0].title == "血糖知识"
        assert "3.9-6.1" in results[0].content


def test_agent_tools_available_in_graph():
    """chat 和 interp 图都能拿到 make_tools 产出的工具集"""
    with patch("app.ai.agents.chat_graph.get_chat_model") as mock_model, \
         patch("app.ai.agents.interp_graph.get_chat_model"), \
         patch("app.ai.agents.tools.ai_rag"):
        mock_model.return_value = MagicMock()
        mock_model.return_value.bind_tools.return_value = MagicMock()

        from app.ai.agents.tools import make_tools
        from app.ai.agents.chat_graph import build_chat_graph
        from app.ai.agents.interp_graph import build_interp_graph

        tools = make_tools("H001", MagicMock())
        assert len(tools) == 6

        chat_g = build_chat_graph("H001", MagicMock())
        interp_g = build_interp_graph("H001", MagicMock())
        assert chat_g is not None
        assert interp_g is not None
