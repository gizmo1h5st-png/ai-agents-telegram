import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Old single bot (keep for backward compat)
    TELEGRAM_BOT_TOKEN: str = ""
    ALLOWED_USERS: str = ""
    
    # Multi-bot tokens
    BOT_COORDINATOR_TOKEN: str = ""
    BOT_RESEARCHER_TOKEN: str = ""
    BOT_CRITIC_TOKEN: str = ""
    BOT_EXECUTOR_TOKEN: str = ""
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://localhost/aiagents"
    REDIS_URL: str = "redis://localhost:6379"
    
    # LLM
    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    HUGGINGFACE_API_KEY: str = ""
    
    # Models per agent
    DEFAULT_MODEL: str = "deepseek/deepseek-v4-flash:free"
    COORDINATOR_MODEL: str = ""
    RESEARCHER_MODEL: str = ""
    CRITIC_MODEL: str = ""
    EXECUTOR_MODEL: str = ""
    
    # Limits
    MAX_STEPS_PER_TASK: int = 50
    MAX_CONTEXT_MESSAGES: int = 15
    MAX_TOKENS_PER_REQUEST: int = 1024
    DAILY_REQUEST_LIMIT: int = 200
    
    # Loop prevention
    MIN_REPLY_INTERVAL: int = 8
    MAX_DISCUSSION_STEPS: int = 50
    IDLE_TIMEOUT_MINUTES: int = 10

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
    
    @property
    def multi_bot_mode(self) -> bool:
        return bool(self.BOT_COORDINATOR_TOKEN)
    
    def get_agent_model(self, role: str) -> str:
        models = {
            "coordinator": self.COORDINATOR_MODEL,
            "researcher": self.RESEARCHER_MODEL,
            "critic": self.CRITIC_MODEL,
            "executor": self.EXECUTOR_MODEL,
        }
        return models.get(role, "") or self.DEFAULT_MODEL

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


FREE_MODELS = {
    "deepseek-v4": {"id": "deepseek/deepseek-v4-flash:free", "name": "🚀 DeepSeek V4 Flash", "desc": "Быстрая", "provider": "openrouter"},
    "deepseek-r1": {"id": "deepseek/deepseek-r1:free", "name": "🧠 DeepSeek R1", "desc": "Reasoning", "provider": "openrouter"},
    "deepseek-chat": {"id": "deepseek/deepseek-chat-v3-0324:free", "name": "💬 DeepSeek Chat V3", "desc": "Чат", "provider": "openrouter"},
    "llama4": {"id": "meta-llama/llama-4-maverick:free", "name": "🦙 Llama 4 Maverick", "desc": "1M контекст", "provider": "openrouter"},
    "qwen3": {"id": "qwen/qwen3-235b-a22b:free", "name": "🌟 Qwen3 235B", "desc": "Код и анализ", "provider": "openrouter"},
    "qwen-coder": {"id": "qwen/qwen3-coder:free", "name": "💻 Qwen3 Coder", "desc": "Для кода", "provider": "openrouter"},
    "grok-mini": {"id": "x-ai/grok-3-mini-beta:free", "name": "⚡ Grok 3 Mini", "desc": "Быстрый", "provider": "openrouter"},
    "gemma4": {"id": "google/gemma-4-31b-it:free", "name": "💎 Gemma 4 31B", "desc": "Google", "provider": "openrouter"},
    "gemma3": {"id": "google/gemma-3-27b-it:free", "name": "💎 Gemma 3 27B", "desc": "Лёгкая", "provider": "openrouter"},
    "mistral": {"id": "mistralai/mistral-small-3.1-24b-instruct:free", "name": "🌀 Mistral 24B", "desc": "Баланс", "provider": "openrouter"},
    "nemotron-120b": {"id": "nvidia/nemotron-3-super-120b-a12b:free", "name": "🟢 Nemotron 120B", "desc": "NVIDIA", "provider": "openrouter"},
    "nemotron-30b": {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "name": "🟢 Nemotron 30B", "desc": "Reasoning", "provider": "openrouter"},
    "glm4": {"id": "zhipu-ai/glm-4-32b:free", "name": "🇨🇳 GLM-4 32B", "desc": "Мультиязычная", "provider": "openrouter"},
    "glm45": {"id": "z-ai/glm-4.5-air:free", "name": "🇨🇳 GLM-4.5 Air", "desc": "Агенты", "provider": "openrouter"},
    "hermes": {"id": "nousresearch/hermes-3-llama-3.1-70b:free", "name": "🔮 Hermes 3 70B", "desc": "Ролевые", "provider": "openrouter"},
    "gpt-120b": {"id": "openai/gpt-oss-120b:free", "name": "🤖 GPT-OSS 120B", "desc": "Большая", "provider": "openrouter"},
    "gpt-20b": {"id": "openai/gpt-oss-20b:free", "name": "🤖 GPT-OSS 20B", "desc": "Компактная", "provider": "openrouter"},
    "trinity": {"id": "arcee-ai/trinity-large-thinking:free", "name": "🔺 Trinity", "desc": "Thinking", "provider": "openrouter"},
    "laguna": {"id": "poolside/laguna-xs.2:free", "name": "🏊 Laguna", "desc": "Poolside", "provider": "openrouter"},
    "owl": {"id": "openrouter/owl-alpha", "name": "🦉 Owl Alpha", "desc": "Экспериментальная", "provider": "openrouter"},
    "lfm": {"id": "liquid/lfm-2.5-1.2b-instruct:free", "name": "💧 LFM", "desc": "Liquid AI", "provider": "openrouter"},
    "hf-deepseek": {"id": "deepseek-ai/DeepSeek-R1", "name": "🧠 HF DeepSeek R1", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-llama": {"id": "meta-llama/Llama-3.1-8B-Instruct", "name": "🦙 HF Llama 3.1", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-qwen": {"id": "Qwen/Qwen2.5-72B-Instruct", "name": "🌟 HF Qwen 72B", "desc": "HuggingFace", "provider": "huggingface"},
}


AGENT_BOTS = {
    "coordinator": {
        "emoji": "🎯",
        "name": "Координатор",
        "prompt": "Ты — Координатор команды ИИ-агентов в групповом чате.\nТы видишь сообщения других ботов и пользователя.\nТвоя задача — управлять обсуждением.\nНазначай: @researcher_bot, @critic_bot, @executor_bot\nНЕ давай [ФИНАЛЬНЫЙ ОТВЕТ] раньше шага 6.\nСначала пусть команда обсудит. Максимум 3 предложения."
    },
    "researcher": {
        "emoji": "🔍",
        "name": "Исследователь",
        "prompt": "Ты — Исследователь в команде ИИ-агентов.\nТы в групповом чате с другими ботами.\nСобирай информацию, давай факты.\nДля поиска: [SEARCH: запрос]\nПередай слово @critic_bot или @coordinator_bot."
    },
    "critic": {
        "emoji": "🧐",
        "name": "Критик",
        "prompt": "Ты — Критик в команде ИИ-агентов.\nТы видишь что пишут другие боты.\nПроверяй решения: ✅ / ⚠️ / ❌\nПредлагай улучшения. Передай @coordinator_bot."
    },
    "executor": {
        "emoji": "⚡",
        "name": "Исполнитель",
        "prompt": "Ты — Исполнитель в команде ИИ-агентов.\nДелай конкретную работу: код, тексты, расчёты.\nДавай готовый результат. Передай @critic_bot."
    },
}
