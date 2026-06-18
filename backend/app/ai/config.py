from pymilvus import connections

from app.config import settings

VECTOR_DIM = 1024  # bge-m3 / text-embedding-v3 均为 1024

_milvus_started = False


def ensure_milvus_started():
    """启动 milvus-lite 嵌入式服务并建立连接（从旧 core/milvus.py 搬迁）"""
    global _milvus_started
    if _milvus_started:
        return
    from milvus_lite import server_manager
    server_manager.start(port=settings.MILVUS_PORT)
    connections.connect(host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
    _milvus_started = True


def get_embedding_model():
    """根据 EMBED_PROVIDER 构造 LlamaIndex Embedding 模型"""
    from llama_index.embeddings.openai import OpenAIEmbedding

    if settings.EMBED_PROVIDER == "remote":
        return OpenAIEmbedding(
            api_base=settings.REMOTE_EMBED_BASE_URL,
            api_key=settings.REMOTE_EMBED_API_KEY,
            model_name=settings.REMOTE_EMBED_MODEL,
            embed_dim=VECTOR_DIM,
        )
    return OpenAIEmbedding(
        api_base=settings.EMBED_BASE_URL,
        api_key="not-required",
        model_name=settings.EMBED_MODEL_NAME,
        embed_dim=VECTOR_DIM,
    )
