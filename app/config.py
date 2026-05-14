import os
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    ALLOWED_USERS: str = ""
    DATABASE_URL: str = "postgresql+asyncpg://localhost/aiagents"
    REDIS_URL: str = "redis://localhost:6379"
    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL: str = "deepseek/deepseek-v4-flash:free"
    SUMMARIZER_MODEL: str = "google/gemma-2-9b-it:free"
    MAX_STEPS_PER_TASK: int = 25
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
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

FREE_MODELS = {
    "deepseek-v4": {
        "id": "deepseek/deepseek-v4-flash:free",
        "name": "🚀 DeepSeek V4 Flash",
        "desc": "Новейшая быстрая модель"
    },
    "deepseek-r1": {
        "id": "deepseek/deepseek-r1:free",
        "name": "🧠 DeepSeek R1",
        "desc": "Reasoning модель"
    },
    "gemma-4-31b": {
        "id": "google/gemma-4-31b-it:free",
        "name": "💎 Gemma 4 31B",
        "desc": "Большая от Google"
    },
    "gemma-4-26b": {
        "id": "google/gemma-4-26b-a4b-it:free",
        "name": "💎 Gemma 4 26B",
        "desc": "Эффективная от Google"
    },
    "llama4": {
        "id": "meta-llama/llama-4-maverick:free",
        "name": "🦙 Llama 4 Maverick",
        "desc": "Новейшая от Meta"
    },
    "qwen": {
        "id": "qwen/qwen3-235b-a22b:free",
        "name": "🌟 Qwen3 235B",
        "desc": "Огромная от Alibaba"
    },
    "nemotron": {
        "id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "name": "🟢 Nemotron 30B",
        "desc": "Reasoning от NVIDIA"
    },
    "trinity": {
        "id": "arcee-ai/trinity-large-thinking:free",
        "name": "🔺 Trinity Thinking",
        "desc": "Глубокое мышление"
    },
    "ring": {
        "id": "inclusionai/ring-2.6-1t:free",
        "name": "💍 Ring 2.6 1T",
        "desc": "Триллионная модель"
    },
    "laguna": {
        "id": "poolside/laguna-xs.2:free",
        "name": "🏊 Laguna XS.2",
        "desc": "От Poolside"
    },
    "cobuddy": {
        "id": "baidu/cobuddy:free",
        "name": "🐼 CoBuddy",
        "desc": "От Baidu"
    },
    "owl": {
        "id": "openrouter/owl-alpha",
        "name": "🦉 Owl Alpha",
        "desc": "Экспериментальная"
    },
}
