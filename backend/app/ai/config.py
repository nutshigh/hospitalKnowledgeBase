from pymilvus import connections

from app.config import settings

VECTOR_DIM = 1024  # bge-m3 / text-embedding-v3 均为 1024

_milvus_started = False


def ensure_milvus_started():
    """连接独立部署的 Milvus 服务。

    Milvus 以独立进程/容器运行（非嵌入式 milvus-lite），所有应用进程
    （backend + workers）共享同一个远端服务，无文件锁/端口冲突。
    连接 URI 由 settings.MILVUS_URI 提供（默认 http://localhost:19530）。
    """
    global _milvus_started
    if _milvus_started:
        return
    connections.connect(alias="default", uri=settings.MILVUS_URI)
    _milvus_started = True


def get_milvus_uri() -> str:
    """获取 Milvus 服务的 URI"""
    return settings.MILVUS_URI


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
