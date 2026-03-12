from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application configuration settings."""
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:HalD%4000**@localhost:5432/vx_db_1"
    
    # Application
    APP_NAME: str = "File Processing Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    
    # File Upload
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB
    
    # Logging
    LOG_FILE: str = "pipeline.log"
    LOG_LEVEL: str = "INFO"
    
    # External Service
    SPLIT_SERVICE_URL: str = "http://localhost:8083/identify"
    SPLIT_SERVICE_TIMEOUT: int = 300  # 5 minutes timeout
    DEFAULT_CHANNEL_ID: str = "default_channel"

    # Webhook
    WEBHOOK_URL: str = "http://localhost:8001/webhook/vx/v1/result"
    WEBHOOK_TIMEOUT: int = 30  # seconds
    
    # AWS / S3
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-south-1"
    BUCKET_NAME: str = ""

    # Request ID generation
    REQ_ID_LENGTH: int = 8
    REQ_ID_ALPHABET: str = "abcdefghijklmnopqrstuvwxyz0123456789-_.~"

    # Auth middleware
    CLIENT_CONFIGS_JSON: str = "{}"
    AUTH_ENABLED: bool = True

    # Supporting-doc Gemini cache (used by downstream services; kept here so .env loads cleanly)
    SUPPORTING_DOC_CACHE_ID: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # silently ignore .env keys not declared above


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def setup_logging():
    """Setup logging to route all application and library logs through the custom JSON logger."""
    import logging
    from app.logger import customLogger, JsonFormatter, LevelFilter, ALLOWED_LOG_LEVELS

    # Prevent app_logger from propagating to root to avoid duplicate log entries
    customLogger.logger.propagate = False

    # Configure root logger with the same JSON formatter so all other loggers
    # (uvicorn, sqlalchemy, logging.getLogger(__name__) calls, etc.) also emit
    # structured JSON through the same pipeline.
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Clear any plain-text handlers added by basicConfig or uvicorn before this
    # call, then install our JsonFormatter so all library loggers emit structured JSON.
    root_logger.handlers.clear()
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(JsonFormatter())
    console_handler.addFilter(LevelFilter(ALLOWED_LOG_LEVELS))
    root_logger.addHandler(console_handler)

    # Reduce noise from chatty libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
