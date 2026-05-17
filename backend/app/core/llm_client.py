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


class LLMClient:
    def __init__(self, base_url: str = settings.VLLM_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.model = settings.VLLM_CHAT_MODEL
        self.client = Client(timeout=Timeout(300.0))

    def chat(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 1024) -> str:
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

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
