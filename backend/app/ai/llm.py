import asyncio
import os

from langchain_openai import ChatOpenAI

from app.config import settings

_MEDGO_MAX = int(os.getenv("MEDGO_MAX_CONCURRENCY", "2"))
medgo_sem = asyncio.Semaphore(_MEDGO_MAX)


async def _guarded(coro):
    """统一 MedGo 并发计数闸。所有 MedGo 调用必须经此包装。"""
    async with medgo_sem:
        return await coro


def get_chat_model(streaming: bool = False, no_think: bool = False,
                   request_timeout: float | None = None) -> ChatOpenAI:
    """根据 LLM_PROVIDER 构造 LangChain ChatOpenAI。

    local  → 本地 MedGo (Qwen3-32B 医疗模型) via vLLM serve (OpenAI 兼容接口)
    remote → 远端 OpenAI 兼容 API

    no_think=True 时禁用 Qwen3 thinking(extra_body enable_thinking=false):
    用于"提取/格式化"类任务(指标解析、结论/异常提取),该场景长思考会拖慢
    链路数分钟至数十分钟;解读/聊天等需要思考质量的任务保持默认(False)。
    """
    extra_body = None
    if no_think:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    if settings.LLM_PROVIDER == "remote":
        return ChatOpenAI(
            base_url=settings.REMOTE_LLM_BASE_URL,
            model=settings.REMOTE_LLM_MODEL,
            api_key=settings.REMOTE_LLM_API_KEY,
            temperature=settings.REMOTE_LLM_TEMPERATURE,
            max_tokens=settings.REMOTE_LLM_MAX_TOKENS,
            timeout=request_timeout,
            streaming=streaming,
            extra_body=extra_body,
        )
    # local: MedGo via vLLM
    return ChatOpenAI(
        base_url=settings.MEDGO_BASE_URL,
        model=settings.MEDGO_MODEL,
        api_key=settings.MEDGO_API_KEY,
        temperature=settings.MEDGO_TEMPERATURE,
        max_tokens=settings.MEDGO_MAX_TOKENS,
        timeout=request_timeout,
        streaming=streaming,
        extra_body=extra_body,
    )