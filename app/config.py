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
    "deepseek-v4": {"id": "deepseek/deepseek-v4-flash:free", "name": "🚀 DeepSeek V4 Flash", "desc": "Быстрая и стабильная"},
    "nemotron": {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "name": "🟢 Nemotron 30B", "desc": "Reasoning от NVIDIA"},
    "trinity": {"id": "arcee-ai/trinity-large-thinking:free", "name": "🔺 Trinity Thinking", "desc": "Глубокое мышление"},
    "laguna": {"id": "poolside/laguna-xs.2:free", "name": "🏊 Laguna XS.2", "desc": "От Poolside"},
    "owl": {"id": "openrouter/owl-alpha", "name": "🦉 Owl Alpha", "desc": "Экспериментальная"},
    "minimax": {"id": "minimax/minimax-m2.5:free", "name": "🔷 MiniMax M2.5", "desc": "Модель MiniMax"},
    "lfm-thinking": {"id": "liquid/lfm-2.5-1.2b-thinking:free", "name": "💭 LFM Thinking", "desc": "Thinking от Liquid"},
    "lfm-instruct": {"id": "liquid/lfm-2.5-1.2b-instruct:free", "name": "📝 LFM Instruct", "desc": "Instruct от Liquid"},
    "gpt-oss-120b": {"id": "openai/gpt-oss-120b:free", "name": "🤖 GPT-OSS 120B", "desc": "Большая open-source"},
    "gpt-oss-20b": {"id": "openai/gpt-oss-20b:free", "name": "🤖 GPT-OSS 20B", "desc": "Компактная open-source"},
    "glm-4": {"id": "zhipu-ai/glm-4.5-air:free", "name": "🇨🇳 GLM-4.5 Air", "desc": "От Zhipu AI"},
    "qwen-coder": {"id": "qwen/qwen3-coder:free", "name": "💻 Qwen3 Coder", "desc": "Для кода"},
    "dolphin": {"id": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free", "name": "🐬 Dolphin 24B", "desc": "Uncensored"},
    "hermes": {"id": "nousresearch/hermes-3-llama-3.1-405b:free", "name": "⚡ Hermes 3 405B", "desc": "Огромная от NousResearch"},
}
