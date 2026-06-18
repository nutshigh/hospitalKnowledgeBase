from pymilvus import connections

from app.config import settings

VECTOR_DIM = 1024  # bge-m3 / text-embedding-v3 均为 1024

_milvus_started = False
_milvus_uri: str = ""


def ensure_milvus_started():
    """启动 milvus-lite 嵌入式服务并建立连接（从旧 core/milvus.py 搬迁）"""
    global _milvus_started, _milvus_uri
    if _milvus_started:
        return
    from milvus_lite.server_manager import ServerManager
    _milvus_uri = ServerManager().start_and_get_uri(settings.MILVUS_DATA_DIR)
    connections.connect(uri=_milvus_uri)
    _milvus_started = True


def get_milvus_uri() -> str:
    """获取 milvus-lite 的实际 URI（端口随机分配）"""
    ensure_milvus_started()
    return _milvus_uri


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
