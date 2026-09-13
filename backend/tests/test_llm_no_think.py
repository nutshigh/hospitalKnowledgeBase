from app.ai.llm import get_chat_model


def test_default_no_extra_body():
    """Qwen3 thinking 由 vLLM 模板控制, 客户端不传 extra_body。"""
    m = get_chat_model()
    assert m.extra_body is None
