from unittest.mock import patch, MagicMock


def test_vector_dim_bge_m3():
    """bge-m3 embedding provider 对应 1024 维"""
    with patch("app.config.settings.EMBED_PROVIDER", "local"):
        from app.ai.config import VECTOR_DIM
        assert VECTOR_DIM == 1024


def test_get_embedding_model_local():
    """local provider 返回 OpenAIEmbedding 指向 vLLM"""
    with patch("app.config.settings.EMBED_PROVIDER", "local"):
        from app.ai.config import get_embedding_model
        model = get_embedding_model()
        assert model is not None
