from typing import List
from httpx import Client, Timeout
from app.config import settings


class EmbeddingClient:
    def __init__(self):
        if settings.EMBED_PROVIDER == "remote":
            self.base_url = settings.REMOTE_EMBED_BASE_URL.rstrip("/")
            self.model = settings.REMOTE_EMBED_MODEL
            self.api_key = settings.REMOTE_EMBED_API_KEY
        else:
            self.base_url = settings.EMBED_BASE_URL.rstrip("/")
            self.model = settings.EMBED_MODEL_NAME
            self.api_key = ""
        self.client = Client(timeout=Timeout(60.0))

    def embed(self, texts: List[str]) -> List[List[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = self.client.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model, "input": texts},
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data["data"]]

    def embed_single(self, text: str) -> List[float]:
        results = self.embed([text])
        return results[0]


embedding_client = EmbeddingClient()
