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
    MYSQL_PASSWORD: str = ""
    MYSQL_TEMPLATE_DB: str = "hospital_template"

    # Milvus (Milvus Lite embedded server)
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530

    # RabbitMQ
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"

    # vLLM
    VLLM_BASE_URL: str = "http://localhost:8000/v1"
    VLLM_CHAT_MODEL: str = "qwen2.5"
    VLLM_VISION_MODEL: str = "qwen-vl"
    VLLM_EMBED_MODEL: str = "bge-m3"

    # JWT
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # File Storage
    FILE_STORAGE_ROOT: str = "./storage"

    class Config:
        env_file = ".env"


settings = Settings()
