from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Hospital AI System"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # Logging
    LOG_LEVEL: str = "INFO"  # 控制日志级别;setup_logging() 优先读环境变量 LOG_LEVEL

    # MySQL
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "root"
    MYSQL_TEMPLATE_DB: str = "hospital_template"

    # Milvus (独立部署，非嵌入式 milvus-lite)
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530

    # RabbitMQ
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"

    # Redis (embedding 向量缓存等)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0
    # embedding 缓存 TTL（秒），7 天
    EMBED_CACHE_TTL: int = 604800

# OCR (PaddleOCR-VL-1.5 本地部署 via paddle_ocr_service, 端口 8001)
    OCR_BASE_URL: str = "http://localhost:8001"
    OCR_MODEL: str = "PaddlePaddle/PaddleOCR-VL-1.5"
    OCR_PROMPT: str = ""  # PaddleOCR pipeline 模式下未使用，保留兼容

    # vLLM (OpenAI-compatible API)
    # 注：文本对话 LLM 已由 MEDGO_* 取代；以下 VLLM_* 仅保留兼容，不再用于 chat。
    VLLM_BASE_URL: str = "http://localhost:8000/v1"
    VLLM_CHAT_MODEL: str = "qwen2.5"
    VLLM_VISION_MODEL: str = "qwen-vl"
    VLLM_EMBED_MODEL: str = "bge-m3"

    # 本地文本 LLM (MedGo via vLLM serve, OpenAI 兼容接口)
    MEDGO_BASE_URL: str = "http://localhost:8004/v1"
    MEDGO_MODEL: str = "/data/models/MedGo"  # 本地权重路径；也可填 OpenMedZoo/MedGo 让 vLLM 自动拉取
    MEDGO_MAX_TOKENS: int = 4096
    MEDGO_TEMPERATURE: float = 0.1
    MEDGO_API_KEY: str = "not-required"  # vLLM 本地服务无鉴权

    # LLM Provider
    LLM_PROVIDER: str = "local"  # local (MedGo via vLLM) | remote

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

    # Neo4j / KnowledgeGraph
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "medgraph123"
    KG_ENABLED: bool = True                    # KG 检索总开关
    KG_TOP_K: int = 3                          # KG 检索返回数
    CM3KG_DATA_PATH: str = "/data/data/medical.csv"  # CM3KG 数据文件

    # Agent
    AGENT_MAX_ITERATIONS: int = 8              # 单轮最大工具调用轮数
    JUDGE_MAX_RETRIES: int = 2                 # Judge 审核不通过的最大重试次数

    # JWT
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # Tenant admin (POST /api/v1/tenants)
    # 空 = 接口完全开放(开发环境);填值 = 要求请求头 X-Admin-Token 匹配
    ADMIN_TOKEN: str = ""

    # Batch Import (spec §6.2)
    MEDGO_MAX_CONCURRENCY: int = 2
    BATCH_ARCHIVE_MAX_SIZE: int = 10737418240  # 10GB
    BATCH_CHUNK_SIZE: int = 5242880            # 5MB
    BATCH_CHUNK_TIMEOUT: int = 7200            # 2h,孤儿 uploading 阈值
    BATCH_SWEEP_INTERVAL: int = 300            # 5min
    BATCH_SWEEP_STALL_THRESHOLD: int = 1800    # 30min
    BULK_WINDOW_START: int = 22
    BULK_WINDOW_END: int = 8
    BATCH_FILE_MAX_SIZE: int = 52428800        # 50MB
    DEAD_LETTER_TTL: int = 604800              # 7d

    # File Storage
    FILE_STORAGE_ROOT: str = "./storage"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
