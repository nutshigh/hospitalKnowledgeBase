from langchain_openai import ChatOpenAI

from app.config import settings


def get_chat_model(streaming: bool = False) -> ChatOpenAI:
    """根据 LLM_PROVIDER 构造 LangChain ChatOpenAI。

    local  → 本地 MedGo (Qwen3-32B 医疗模型) via vLLM serve (OpenAI 兼容接口)
    remote → 远端 OpenAI 兼容 API
    """
    if settings.LLM_PROVIDER == "remote":
        return ChatOpenAI(
            base_url=settings.REMOTE_LLM_BASE_URL,
            model=settings.REMOTE_LLM_MODEL,
            api_key=settings.REMOTE_LLM_API_KEY,
            temperature=settings.REMOTE_LLM_TEMPERATURE,
            max_tokens=settings.REMOTE_LLM_MAX_TOKENS,
            timeout=None,
            streaming=streaming,
        )
    # local: MedGo via vLLM
    return ChatOpenAI(
        base_url=settings.MEDGO_BASE_URL,
        model=settings.MEDGO_MODEL,
        api_key=settings.MEDGO_API_KEY,
        temperature=settings.MEDGO_TEMPERATURE,
        max_tokens=settings.MEDGO_MAX_TOKENS,
        timeout=None,
        streaming=streaming,
    )