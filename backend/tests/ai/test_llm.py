from unittest.mock import patch


def test_get_chat_model_local():
    """local provider 返回 ChatOpenAI 指向 vLLM"""
    with patch("app.config.settings.LLM_PROVIDER", "local"):
        from app.ai.llm import get_chat_model
        model = get_chat_model()
        assert model.model_name == "qwen2.5" or model.model == "qwen2.5"


def test_get_chat_model_remote():
    """remote provider 返回 ChatOpenAI 指向远端 API"""
    with patch("app.config.settings.LLM_PROVIDER", "remote"), \
         patch("app.config.settings.REMOTE_LLM_API_KEY", "sk-test-key"):
        from app.ai.llm import get_chat_model
        model = get_chat_model(streaming=True)
        assert model.streaming is True
