from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "AI Tutor"
    DEBUG: bool = True

    # Dev mode: uses SQLite + local storage + sync tasks (no Docker needed)
    DEV_MODE: bool = True

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./ai_tutor.db"

    # Redis (not needed in dev mode)
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO / S3 (not needed in dev mode, uses local storage instead)
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "ai-tutor"
    MINIO_SECURE: bool = False
    MINIO_PUBLIC_ENDPOINT: str = "localhost:9000"

    # Local storage (used when MinIO is unavailable or DEV_MODE=true)
    LOCAL_STORAGE_DIR: str = "./uploads"

    # JWT
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # Qdrant (optional, may not be available in dev mode)
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333

    # LLM
    LLM_API_KEY: str = ""
    LLM_API_BASE: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o"
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    # File Upload
    MAX_UPLOAD_SIZE_MB: int = 50

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
