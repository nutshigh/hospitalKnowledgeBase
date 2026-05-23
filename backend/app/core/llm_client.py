from abc import ABC, abstractmethod
from typing import Iterator
from httpx import Client, Timeout
from app.config import settings

SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，
为体检者撰写易懂的指标解读和健康建议。

规则:
1. 绿区指标一笔带过，重点解读红区和黄区
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统的"注意饮食"
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"
"""


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], temperature: float, max_tokens: int) -> str: ...

    @abstractmethod
    def chat_stream(self, messages: list[dict], temperature: float, max_tokens: int) -> Iterator[str]: ...


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 API 实现 — vLLM 和远端 API 统一使用此 Provider"""
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = Client(timeout=Timeout(connect=10.0, read=float(timeout), write=30.0, pool=10.0))

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 1024) -> str:
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def chat_stream(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 1024) -> Iterator[str]:
        with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    import json
                    try:
                        delta = json.loads(data_str)["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue


class LLMClient:
    def __init__(self):
        if settings.LLM_PROVIDER == "remote":
            self._provider = OpenAICompatProvider(
                base_url=settings.REMOTE_LLM_BASE_URL,
                model=settings.REMOTE_LLM_MODEL,
                api_key=settings.REMOTE_LLM_API_KEY,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
        else:
            self._provider = OpenAICompatProvider(
                base_url=settings.VLLM_BASE_URL,
                model=settings.VLLM_CHAT_MODEL,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )

    def chat(self, messages: list[dict], stream: bool = False,
             temperature: float | None = None, max_tokens: int | None = None) -> str | Iterator[str]:
        temp = temperature if temperature is not None else (settings.REMOTE_LLM_TEMPERATURE if settings.LLM_PROVIDER == "remote" else 0.1)
        mt = max_tokens if max_tokens is not None else (settings.REMOTE_LLM_MAX_TOKENS if settings.LLM_PROVIDER == "remote" else 1024)
        if stream:
            return self._provider.chat_stream(messages, temperature=temp, max_tokens=mt)
        return self._provider.chat(messages, temperature=temp, max_tokens=mt)

    def interpret_indicator(self, indicator: dict, knowledge_context: str) -> str:
        prompt = f"""## 本次报告数据
| 指标 | 结果 | 参考区间 | 判定 |
|------|------|----------|------|
| {indicator.get('item_name', '')} | {indicator.get('result_value', '')} | {indicator.get('ref_range_low', '')}-{indicator.get('ref_range_high', '')} | {indicator.get('deviation', '')}({indicator.get('color_level', '')}) |

## 参考知识库
{knowledge_context if knowledge_context else '无相关知识库条目'}

请解读这个指标，给出健康建议。"""
        return self.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])

    def generate_summary(self, report_summary: str, knowledge_context: str) -> str:
        prompt = f"""## 报告概况
{report_summary}

## 参考知识库
{knowledge_context if knowledge_context else '无相关知识库条目'}

请生成综合健康小结。"""
        return self.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])


llm_client = LLMClient()
