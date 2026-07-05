"""后置 Citation 注入：基于 embedding 相似度自动标注来源。

LLM 正常生成文本（不要求输出 [n]），生成后由本模块：
1. 按句号/分号切分为 sentences
2. 对每个 sentence 与每个 source chunk 算 embedding 余弦相似度
3. 相似度超阈值的句子自动注入 [n] 标注
4. 返回标注后的文本 + citations 列表

不依赖 LLM 行为，确定性结果。
"""
import logging
import math
import re
from typing import List, Optional

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

# 相似度阈值：高于此值则认为句子来源于该 chunk
SIMILARITY_THRESHOLD = 0.6
# embedding 服务地址（复用 BGE-M3）
EMBED_BASE_URL = settings.EMBED_BASE_URL
EMBED_MODEL = settings.EMBED_MODEL_NAME


class Citation(BaseModel):
    ref_id: int
    entry_id: Optional[int] = None
    title: str = ""
    source: str = "document"  # "document" | "knowledge_graph"
    content: str = ""  # 匹配到的来源 chunk 文本片段


def _split_sentences(text: str) -> list[str]:
    """按中文句号/英文句号/分号/换行切分句子，保留分隔符。"""
    # 按句末标点切分，保留标点
    parts = re.split(r'(?<=[。．！？；;!\?])\s*|\n+', text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 3]


def _get_embeddings(texts: list[str]) -> list[list[float]]:
    """调用 BGE-M3 embedding 服务，批量获取向量。"""
    if not texts:
        return []
    try:
        resp = httpx.Client(timeout=30.0).post(
            f"{EMBED_BASE_URL}/embeddings",
            json={"model": EMBED_MODEL, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]
    except Exception as e:
        logger.warning("citation_matcher embedding failed: %s", e)
        return []


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def inject_citations(
    text: str,
    sources: list[dict],
    threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[str, list[dict]]:
    """对文本做后置引用注入。

    Args:
        text: LLM 生成的原始文本（不含 [n] 标记）
        sources: 来源列表，每项含 {entry_id, title, content, source}
                 content 是来源 chunk 的文本
        threshold: 相似度阈值

    Returns:
        (annotated_text, citations)
        - annotated_text: 注入 [n] 标注后的文本
        - citations: 引用列表 [{ref_id, entry_id, title, source, content}]
    """
    if not text or not sources:
        return text, []

    # 切分句子
    sentences = _split_sentences(text)
    if not sentences:
        return text, []

    # 准备 source 文本（去掉 content 为空的）
    source_texts = []
    source_indices = []
    for i, s in enumerate(sources):
        content = s.get("content", "") or s.get("title", "")
        if content:
            source_texts.append(content[:500])  # 截断防止过长
            source_indices.append(i)

    if not source_texts:
        return text, []

    # 批量获取 embeddings：sentences + source_texts 一起算
    all_texts = sentences + source_texts
    all_embeddings = _get_embeddings(all_texts)
    if len(all_embeddings) != len(all_texts):
        logger.warning("citation_matcher: embedding count mismatch, skipping")
        return text, []

    sent_embs = all_embeddings[:len(sentences)]
    source_embs = all_embeddings[len(sentences):]

    # 对每个句子找最佳匹配 source
    citations = []
    citation_map = {}  # sentence_index -> source_index (in sources list)
    used_sources = {}  # source_index -> ref_id

    for si, sent_emb in enumerate(sent_embs):
        best_sim = 0.0
        best_source_idx = -1
        for ssi, src_emb in enumerate(source_embs):
            sim = _cosine_similarity(sent_emb, src_emb)
            if sim > best_sim:
                best_sim = sim
                best_source_idx = source_indices[ssi]

        if best_sim >= threshold and best_source_idx >= 0:
            citation_map[si] = best_source_idx
            if best_source_idx not in used_sources:
                ref_id = len(used_sources) + 1
                used_sources[best_source_idx] = ref_id
                src = sources[best_source_idx]
                citations.append({
                    "ref_id": ref_id,
                    "entry_id": src.get("entry_id"),
                    "title": src.get("title", ""),
                    "source": src.get("source", "document"),
                    "content": (src.get("content", "") or "")[:200],
                })

    # 按 ref_id 排序 citations
    citations.sort(key=lambda c: c["ref_id"])

    # 重建文本，在匹配的句子末尾注入 [n]
    if not citation_map:
        return text, []

    # 重建：遍历原始文本，逐句追加标注
    result_parts = []
    sent_idx = 0
    # 用正则重新遍历原始文本，保留分隔符
    pattern = re.compile(r'(?<=[。．！？；;!\?])\s*|\n+')
    last_end = 0
    for m in pattern.finditer(text):
        segment = text[last_end:m.start()]
        if segment.strip() and len(segment.strip()) > 3:
            if sent_idx in citation_map:
                src_idx = citation_map[sent_idx]
                ref_id = used_sources[src_idx]
                segment = segment.rstrip() + f"[{ref_id}]"
            sent_idx += 1
        result_parts.append(segment)
        result_parts.append(m.group())
        last_end = m.end()
    # 剩余部分
    remaining = text[last_end:]
    if remaining.strip() and len(remaining.strip()) > 3:
        if sent_idx in citation_map:
            src_idx = citation_map[sent_idx]
            ref_id = used_sources[src_idx]
            remaining = remaining.rstrip() + f"[{ref_id}]"
    result_parts.append(remaining)

    annotated_text = "".join(result_parts)
    return annotated_text, citations
