from typing import List
from httpx import Client, Timeout


class EmbeddingClient:
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.client = Client(timeout=Timeout(30.0))

    def embed(self, texts: List[str]) -> List[List[float]]:
        response = self.client.post(
            f"{self.base_url}/api/embed",
            json={"model": "bge-m3", "input": texts},
        )
        response.raise_for_status()
        data = response.json()
        return [item["embedding"] for item in data["embeddings"]]

    def embed_single(self, text: str) -> List[float]:
        results = self.embed([text])
        return results[0]


embedding_client = EmbeddingClient()
