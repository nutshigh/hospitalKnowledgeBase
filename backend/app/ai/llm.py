from langchain_openai import ChatOpenAI

from app.config import settings


def get_chat_model(streaming: bool = False) -> ChatOpenAI:
    """根据 LLM_PROVIDER 构造 LangChain ChatOpenAI，兼容 vLLM/远端"""
    if settings.LLM_PROVIDER == "remote":
        return ChatOpenAI(
            base_url=settings.REMOTE_LLM_BASE_URL,
            model=settings.REMOTE_LLM_MODEL,
            api_key=settings.REMOTE_LLM_API_KEY,
            temperature=settings.REMOTE_LLM_TEMPERATURE,
            max_tokens=settings.REMOTE_LLM_MAX_TOKENS,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            streaming=streaming,
        )
    return ChatOpenAI(
        base_url=settings.VLLM_BASE_URL,
        model=settings.VLLM_CHAT_MODEL,
        api_key="not-required",
        temperature=0.1,
        max_tokens=1024,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        streaming=streaming,
    )
