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
    SPLIT_SERVICE_URL: str = "http://localhost:8900/split"
    SPLIT_SERVICE_TIMEOUT: int = 300  # 5 minutes timeout
    DEFAULT_CHANNEL_ID: str = "default_channel"

    # Webhook
    WEBHOOK_URL: str = "http://localhost:8001/webhook/vx/v1/result"
    WEBHOOK_TIMEOUT: int = 30  # seconds
    
    # AWS Configuration (optional - boto3 uses environment vars or IAM roles)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-south-1"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


def setup_logging():
    """Setup logging configuration for the application."""
    import logging
    from logging.handlers import RotatingFileHandler
    
    settings = get_settings()
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Setup file handler with rotation
    file_handler = RotatingFileHandler(
        settings.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    
    # Setup console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Reduce noise from some libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
