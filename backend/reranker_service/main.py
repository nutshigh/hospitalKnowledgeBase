import os
from typing import List

from fastapi import FastAPI
from pydantic import BaseModel, Field

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

app = FastAPI(title="Reranker Service")

_model = None


def _get_model():
    global _model
    if _model is None:
        from FlagEmbedding import FlagReranker
        model_name = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
        _model = FlagReranker(model_name, use_fp16=True)
    return _model


class RerankRequest(BaseModel):
    query: str
    documents: List[str]
    top_n: int = Field(default=5, ge=1)


class RerankResult(BaseModel):
    index: int
    score: float
    document: str


class RerankResponse(BaseModel):
    results: List[RerankResult]


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    model = _get_model()
    pairs = [[req.query, doc] for doc in req.documents]
    scores = model.compute_score(pairs, normalize=True)
    if not isinstance(scores, list):
        scores = [scores]
    ranked = sorted(
        enumerate(scores), key=lambda x: x[1], reverse=True
    )[:req.top_n]
    return RerankResponse(results=[
        RerankResult(index=i, score=float(s), document=req.documents[i])
        for i, s in ranked
    ])


@app.get("/health")
def health():
    return {"status": "ok"}
