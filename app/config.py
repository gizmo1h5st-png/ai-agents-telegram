import os
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    ALLOWED_USERS: str = ""
    
    # Database - Railway дает postgres://, нам нужен postgresql+asyncpg://
    DATABASE_URL: str = "postgresql+asyncpg://localhost/aiagents"
    REDIS_URL: str = "redis://localhost:6379"
    
    # OpenRouter
    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    
    # Models
    DEFAULT_MODEL: str = "deepseek/deepseek-chat"
    SUMMARIZER_MODEL: str = "google/gemma-2-9b-it:free"
    
    # Limits
    MAX_STEPS_PER_TASK: int = 10
    MAX_CONTEXT_MESSAGES: int = 15
    MAX_TOKENS_PER_REQUEST: int = 1024
    DAILY_REQUEST_LIMIT: int = 200
    
    @property
    def allowed_user_ids(self) -> list[int]:
        if not self.ALLOWED_USERS:
            return []
        return [int(uid.strip()) for uid in self.ALLOWED_USERS.split(",") if uid.strip()]
    
    @property
    def async_database_url(self) -> str:
        """Конвертирует Railway DATABASE_URL в asyncpg формат"""
        url = self.DATABASE_URL
        # Railway дает postgres://, SQLAlchemy async нужен postgresql+asyncpg://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
