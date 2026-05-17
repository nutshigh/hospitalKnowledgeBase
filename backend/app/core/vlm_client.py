import json
from httpx import Client, Timeout
from app.config import settings


SYSTEM_PROMPT = """你是体检报告结构化提取助手。从提供的体检报告图片中精确提取信息。
遵守以下规则:
1. 个人信息（姓名、性别、年龄、检查日期）尽可能提取
2. 每个检验项精确提取: 项目名称、结果值、单位、参考区间
3. 不遗漏任何检验项
4. 无法识别的内容标记为 null
5. 仅返回 JSON，不包含任何解释文字

输出格式:
{
  "personal_info": { "name": null, "gender": null, "age": null, "check_date": null },
  "indicators": [
    { "item_name": "...", "result": "...", "unit": "...", "ref_low": "...", "ref_high": "..." }
  ]
}"""


class VLMClient:
    def __init__(self, base_url: str = settings.VLLM_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.model = settings.VLLM_VISION_MODEL
        self.client = Client(timeout=Timeout(300.0))

    def extract_from_image(self, image_base64: str) -> dict:
        """Extract structured data from a single image using VLM."""
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请提取这份体检报告的信息"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                        ],
                    },
                ],
                "temperature": 0,
                "max_tokens": 4096,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        # vLLM may wrap JSON in markdown code blocks
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("\n", 1)[0]
        return json.loads(content)

    def extract_from_images(self, images_base64: list[str]) -> dict:
        """Extract from multiple images (multi-page PDF) and merge results."""
        all_indicators = []
        personal_info = {}
        for img in images_base64:
            result = self.extract_from_image(img)
            if result.get("personal_info"):
                for k, v in result["personal_info"].items():
                    if v is not None:
                        personal_info[k] = v
            if result.get("indicators"):
                all_indicators.extend(result["indicators"])
        return {"personal_info": personal_info, "indicators": all_indicators}


vlm_client = VLMClient()
