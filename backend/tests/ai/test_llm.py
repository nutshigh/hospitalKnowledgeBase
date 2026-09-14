from unittest.mock import patch


def test_get_chat_model_local():
    """local provider 返回 ChatOpenAI 指向本地 MedGo via vLLM"""
    with patch("app.config.settings.LLM_PROVIDER", "local"):
        from app.ai.llm import get_chat_model
        model = get_chat_model()
        name = model.model_name if hasattr(model, "model_name") else model.model
        assert "MedGo" in name


def test_get_chat_model_remote():
    """remote provider 返回 ChatOpenAI 指向远端 API"""
    with patch("app.config.settings.LLM_PROVIDER", "remote"), \
         patch("app.config.settings.REMOTE_LLM_API_KEY", "sk-test-key"):
        from app.ai.llm import get_chat_model
        model = get_chat_model(streaming=True)
        assert model.streaming is True


def test_get_chat_model_local_disables_sdk_retries():
    """local: SDK max_retries 默认 0, 防 600s 超时被放大成 30min。"""
    with patch("app.config.settings.LLM_PROVIDER", "local"):
        from app.ai.llm import get_chat_model
        model = get_chat_model()
        assert model.max_retries == 0


def test_get_chat_model_remote_disables_sdk_retries():
    with patch("app.config.settings.LLM_PROVIDER", "remote"), \
         patch("app.config.settings.REMOTE_LLM_API_KEY", "sk-test-key"):
        from app.ai.llm import get_chat_model
        model = get_chat_model()
        assert model.max_retries == 0


def test_get_chat_model_max_retries_overrideable():
    with patch("app.config.settings.LLM_PROVIDER", "local"):
        from app.ai.llm import get_chat_model
        model = get_chat_model(max_retries=3)
        assert model.max_retries == 3

