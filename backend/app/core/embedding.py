from typing import List
from httpx import Client, Timeout
from app.config import settings


class EmbeddingClient:
    def __init__(self, base_url: str = settings.VLLM_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.model = settings.VLLM_EMBED_MODEL
        self.client = Client(timeout=Timeout(60.0))

    def embed(self, texts: List[str]) -> List[List[float]]:
        response = self.client.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data["data"]]

    def embed_single(self, text: str) -> List[float]:
        results = self.embed([text])
        return results[0]


embedding_client = EmbeddingClient()
