"""后置 Citation 注入：基于 embedding 相似度自动标注来源。

LLM 正常生成文本（不要求输出 [n]），生成后由本模块：
1. 按句号/分号切分为 sentences
2. 对每个 sentence 与每个 source chunk 算 embedding 余弦相似度
3. 相似度超阈值的句子自动注入 [n] 标注
4. 返回标注后的文本 + citations 列表

不依赖 LLM 行为，确定性结果。

性能优化：
- source chunk 的 embedding 按内容哈希缓存到 Redis（chunk 在多次检索中复用率高，
  避免重复调用 embedding 服务；句子是 LLM 即时生成的，不缓存）。
- 相似度计算用 numpy 向量化（一次矩阵乘法代替双循环），O(S×C) 但常数极低。
"""
import hashlib
import logging
import re
from typing import List, Optional

import httpx
import numpy as np
from pydantic import BaseModel

from app.config import settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

# 复用连接池的 httpx 客户端（embedding 服务地址固定）
_embed_http = httpx.Client(timeout=30.0)

# 相似度阈值：高于此值则认为句子来源于该 chunk
SIMILARITY_THRESHOLD = 0.6
# embedding 服务地址（复用 BGE-M3）
EMBED_BASE_URL = settings.EMBED_BASE_URL
EMBED_MODEL = settings.EMBED_MODEL_NAME

# Redis 向量缓存 key 前缀
_CACHE_PREFIX = "embed"
# chunk 内容截断长度，与原逻辑一致，防止过长
_MAX_SOURCE_LEN = 500


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


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """调用 BGE-M3 embedding 服务，批量获取向量（无缓存）。"""
    if not texts:
        return []
    try:
        resp = _embed_http.post(
            f"{EMBED_BASE_URL}/embeddings",
            json={"model": EMBED_MODEL, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]
    except Exception as e:
        logger.warning("citation_matcher embedding failed: %s", e)
        return []


def _cache_key(text: str) -> str:
    """根据 chunk 文本内容生成稳定缓存 key。"""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # model 名纳入 key，避免换模型后命中旧向量
    return f"{_CACHE_PREFIX}:{EMBED_MODEL}:{h}"


def _get_source_embeddings(source_texts: list[str]) -> list[Optional[list[float]]]:
    """获取 source chunk 的 embedding，命中 Redis 缓存则直接复用，未命中才调服务并回写。

    返回与 source_texts 等长的列表，元素为 embedding 或 None（失败时）。
    """
    n = len(source_texts)
    results: list[Optional[list[float]]] = [None] * n
    if n == 0:
        return results

    r = redis_client.client
    keys = [_cache_key(t) for t in source_texts]

    # 1. 批量查缓存
    if r is not None:
        try:
            cached = r.mget(keys)
        except Exception as e:
            logger.warning("citation_matcher redis mget failed: %s", e)
            cached = [None] * n
        for i, raw in enumerate(cached):
            if raw:
                try:
                    vec = np.frombuffer(raw, dtype=np.float32)
                    results[i] = vec.tolist()
                except Exception:
                    results[i] = None
    else:
        cached = [None] * n

    # 2. 收集未命中的，批量算 embedding
    miss_indices = [i for i in range(n) if results[i] is None]
    if not miss_indices:
        return results

    miss_texts = [source_texts[i] for i in miss_indices]
    miss_embs = _embed_batch(miss_texts)
    if len(miss_embs) != len(miss_indices):
        logger.warning("citation_matcher: embedding count mismatch for %d misses", len(miss_indices))
        # 把已得到的尽可能填上
        for j, idx in enumerate(miss_indices[:len(miss_embs)]):
            results[idx] = miss_embs[j]
        return results

    # 3. 回写缓存（pipe 批量）
    pipe = None
    if r is not None:
        pipe = r.pipeline(transaction=False)
    for j, idx in enumerate(miss_indices):
        emb = miss_embs[j]
        results[idx] = emb
        if pipe is not None:
            try:
                payload = np.asarray(emb, dtype=np.float32).tobytes()
                pipe.set(keys[idx], payload, ex=settings.EMBED_CACHE_TTL)
            except Exception:
                pass
    if pipe is not None:
        try:
            pipe.execute()
        except Exception as e:
            logger.warning("citation_matcher redis mset failed: %s", e)

    return results


def _cosine_similarity_matrix(sent_embs: np.ndarray, src_embs: np.ndarray) -> np.ndarray:
    """计算 (S, D) 与 (C, D) 的余弦相似度矩阵 (S, C)，向量化。"""
    # L2 归一化
    s_norm = sent_embs / (np.linalg.norm(sent_embs, axis=1, keepdims=True) + 1e-12)
    c_norm = src_embs / (np.linalg.norm(src_embs, axis=1, keepdims=True) + 1e-12)
    return s_norm @ c_norm.T  # (S, C)


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
            source_texts.append(content[:_MAX_SOURCE_LEN])  # 截断防止过长
            source_indices.append(i)

    if not source_texts:
        return text, []

    # source embedding：优先命中 Redis 缓存
    src_emb_list = _get_source_embeddings(source_texts)
    valid_src = [(i, e) for i, e in enumerate(src_emb_list) if e is not None]
    if not valid_src:
        logger.warning("citation_matcher: no valid source embeddings, skipping")
        return text, []

    # sentence embedding：实时算，不缓存（LLM 输出即时生成）
    sent_emb_list = _embed_batch(sentences)
    if len(sent_emb_list) != len(sentences):
        logger.warning("citation_matcher: sentence embedding count mismatch, skipping")
        return text, []

    # 组装 numpy 矩阵
    sent_embs = np.asarray(sent_emb_list, dtype=np.float32)        # (S, D)
    src_embs = np.asarray([e for _, e in valid_src], dtype=np.float32)  # (C, D)
    src_pos = [i for i, _ in valid_src]  # 在 source_texts 中的位置

    # 一次矩阵乘法得到全部相似度
    sims = _cosine_similarity_matrix(sent_embs, src_embs)  # (S, C)

    # 对每个句子找最佳匹配 source
    citations = []
    citation_map = {}  # sentence_index -> source_index (in sources list)
    used_sources = {}  # source_index -> ref_id

    best_src_local = np.argmax(sims, axis=1)  # (S,)
    best_sims = sims[np.arange(sims.shape[0]), best_src_local]  # (S,)

    for si in range(sims.shape[0]):
        sim = float(best_sims[si])
        if sim < threshold:
            continue
        local_idx = int(best_src_local[si])
        best_source_idx = source_indices[src_pos[local_idx]]
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
