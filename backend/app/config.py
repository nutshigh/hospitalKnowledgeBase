from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Hospital AI System"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # MySQL
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "root"
    MYSQL_TEMPLATE_DB: str = "hospital_template"

    # Milvus (Milvus Lite embedded server)
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530

    # RabbitMQ
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"

    # OCR (DeepSeek-OCR-2 本地部署 via vLLM serve)
    OCR_BASE_URL: str = "http://localhost:8001/v1"
    OCR_MODEL: str = "deepseek-ai/DeepSeek-OCR-2"
    OCR_PROMPT: str = ""  # 自定义 OCR prompt，空则用内置默认 prompt

    # vLLM
    VLLM_BASE_URL: str = "http://localhost:8000/v1"
    VLLM_CHAT_MODEL: str = "qwen2.5"
    VLLM_VISION_MODEL: str = "qwen-vl"
    VLLM_EMBED_MODEL: str = "bge-m3"

    # LLM Provider
    LLM_PROVIDER: str = "local"  # local | remote

    # Embedding Provider
    EMBED_PROVIDER: str = "local"  # local (vLLM) | remote (API)
    EMBED_BASE_URL: str = "http://localhost:8002/v1"  # local vLLM embedding server
    EMBED_MODEL_NAME: str = "BAAI/bge-m3"
    # Remote embedding (EMBED_PROVIDER=remote)
    REMOTE_EMBED_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    REMOTE_EMBED_API_KEY: str = ""
    REMOTE_EMBED_MODEL: str = "text-embedding-v3"

    # Remote LLM (OpenAI 兼容 API)
    REMOTE_LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    REMOTE_LLM_API_KEY: str = ""
    REMOTE_LLM_MODEL: str = "deepseek-chat"
    REMOTE_LLM_MAX_TOKENS: int = 4096
    REMOTE_LLM_TEMPERATURE: float = 0.1

    # LLM 通用
    LLM_TIMEOUT_SECONDS: int = 120

    # Reranker Provider
    RERANKER_PROVIDER: str = "local"           # local | remote
    RERANKER_BASE_URL: str = "http://localhost:8003"
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_API_KEY: str = ""                 # remote 时用

    # RAG
    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 72
    RAG_VECTOR_TOP_K: int = 20                 # 向量召回数，rerank 前的候选
    RAG_FINAL_TOP_K: int = 5                   # rerank 后返回数
    RAG_HYBRID_ALPHA: float = 0.5              # vector/BM25 融合权重

    # Agent
    AGENT_MAX_ITERATIONS: int = 8              # 单轮最大工具调用轮数

    # JWT
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # File Storage
    FILE_STORAGE_ROOT: str = "./storage"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
