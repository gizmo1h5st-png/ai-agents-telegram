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
    HUGGINGFACE_API_KEY: str = ""
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

# OpenRouter модели
FREE_MODELS = {
    "deepseek": {"id": "deepseek/deepseek-v4-flash:free", "name": "🚀 DeepSeek V4", "desc": "Быстрая", "provider": "openrouter"},
    "nemotron": {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "name": "🟢 Nemotron 30B", "desc": "NVIDIA", "provider": "openrouter"},
    "trinity": {"id": "arcee-ai/trinity-large-thinking:free", "name": "🔺 Trinity", "desc": "Thinking", "provider": "openrouter"},
    "laguna": {"id": "poolside/laguna-xs.2:free", "name": "🏊 Laguna", "desc": "Poolside", "provider": "openrouter"},
    "owl": {"id": "openrouter/owl-alpha", "name": "🦉 Owl Alpha", "desc": "Эксперимент", "provider": "openrouter"},
    "gpt-120b": {"id": "openai/gpt-oss-120b:free", "name": "🤖 GPT-OSS 120B", "desc": "Большая", "provider": "openrouter"},
    "gpt-20b": {"id": "openai/gpt-oss-20b:free", "name": "🤖 GPT-OSS 20B", "desc": "Компактная", "provider": "openrouter"},
    "lfm": {"id": "liquid/lfm-2.5-1.2b-instruct:free", "name": "💧 LFM", "desc": "Liquid AI", "provider": "openrouter"},
    # Hugging Face модели
    "hf-llama": {"id": "meta-llama/Llama-3.2-3B-Instruct", "name": "🦙 Llama 3.2 3B", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-mistral": {"id": "mistralai/Mistral-7B-Instruct-v0.3", "name": "🌀 Mistral 7B", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-phi": {"id": "microsoft/Phi-3-mini-4k-instruct", "name": "🔬 Phi-3 Mini", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-qwen": {"id": "Qwen/Qwen2.5-3B-Instruct", "name": "🌟 Qwen 2.5 3B", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-gemma": {"id": "google/gemma-2-2b-it", "name": "💎 Gemma 2 2B", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-zephyr": {"id": "HuggingFaceH4/zephyr-7b-beta", "name": "🌬️ Zephyr 7B", "desc": "HuggingFace", "provider": "huggingface"},
}
