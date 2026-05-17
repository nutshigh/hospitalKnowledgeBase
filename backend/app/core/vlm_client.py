import json
from httpx import Client, Timeout


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
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.client = Client(timeout=Timeout(120.0))

    def extract_from_image(self, image_base64: str) -> dict:
        response = self.client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": "qwen-vl",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "请提取这份体检报告的信息", "images": [image_base64]},
                ],
                "format": "json",
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return json.loads(data["message"]["content"])

    def extract_from_images(self, images_base64: list[str]) -> dict:
        all_indicators = []
        personal_info = {}
        for img in images_base64:
            result = self.extract_from_image(img)
            if result.get("personal_info"):
                personal_info = {k: v for k, v in result["personal_info"].items() if v is not None}
            if result.get("indicators"):
                all_indicators.extend(result["indicators"])
        return {"personal_info": personal_info, "indicators": all_indicators}


vlm_client = VLMClient()
