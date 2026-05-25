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
    # === САМЫЕ СТАБИЛЬНЫЕ (проверены май 2026) ===
    "deepseek-r1": {"id": "deepseek/deepseek-r1:free", "name": "🧠 DeepSeek R1", "desc": "Reasoning, логика", "provider": "openrouter"},
    "deepseek-chat": {"id": "deepseek/deepseek-chat-v3-0324:free", "name": "💬 DeepSeek Chat V3", "desc": "Чат, контент", "provider": "openrouter"},
    "deepseek-r1-0528": {"id": "deepseek/deepseek-r1-0528:free", "name": "🧠 DeepSeek R1 0528", "desc": "Новый reasoning", "provider": "openrouter"},
    "deepseek-chat-v31": {"id": "deepseek/deepseek-chat-v3.1:free", "name": "💬 DeepSeek V3.1", "desc": "Новый чат", "provider": "openrouter"},
    "llama4-maverick": {"id": "meta-llama/llama-4-maverick:free", "name": "🦙 Llama 4 Maverick", "desc": "1M контекст", "provider": "openrouter"},
    "llama4-scout": {"id": "meta-llama/llama-4-scout:free", "name": "🦙 Llama 4 Scout", "desc": "Быстрый", "provider": "openrouter"},
    "qwen3": {"id": "qwen/qwen3-235b-a22b:free", "name": "🌟 Qwen3 235B", "desc": "Код и анализ", "provider": "openrouter"},
    "qwen-coder": {"id": "qwen/qwen3-coder-480b-a35b-instruct:free", "name": "💻 Qwen3 Coder 480B", "desc": "Код", "provider": "openrouter"},
    "grok-mini": {"id": "x-ai/grok-3-mini-beta:free", "name": "⚡ Grok 3 Mini", "desc": "Быстрый", "provider": "openrouter"},
    "mistral": {"id": "mistralai/mistral-small-3.1-24b-instruct:free", "name": "🌀 Mistral 24B", "desc": "Баланс", "provider": "openrouter"},
    "gemma3": {"id": "google/gemma-3-27b-it:free", "name": "💎 Gemma 3 27B", "desc": "Google", "provider": "openrouter"},
    "glm4": {"id": "zhipu-ai/glm-4-32b:free", "name": "🇨🇳 GLM-4 32B", "desc": "Мультиязычная", "provider": "openrouter"},
    "glm45": {"id": "zhipu-ai/glm-4.5-air:free", "name": "🇨🇳 GLM-4.5 Air", "desc": "Агенты", "provider": "openrouter"},
    "hermes": {"id": "nousresearch/hermes-3-llama-3.1-70b:free", "name": "🔮 Hermes 70B", "desc": "Ролевые", "provider": "openrouter"},
    "nemotron-nano": {"id": "nvidia/llama-3.1-nemotron-nano-8b-v1:free", "name": "🟢 Nemotron Nano 8B", "desc": "NVIDIA быстрая", "provider": "openrouter"},
    "kimi": {"id": "moonshotai/kimi-vl-a3b-thinking:free", "name": "🌙 Kimi Thinking", "desc": "Thinking", "provider": "openrouter"},
    "devstral": {"id": "mistralai/devstral-2-2512:free", "name": "🌀 Devstral 2", "desc": "Код от Mistral", "provider": "openrouter"},
    # === HuggingFace ===
    "hf-deepseek": {"id": "deepseek-ai/DeepSeek-R1", "name": "🧠 HF DeepSeek R1", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-llama": {"id": "meta-llama/Llama-3.1-8B-Instruct", "name": "🦙 HF Llama 3.1", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-qwen": {"id": "Qwen/Qwen2.5-72B-Instruct", "name": "🌟 HF Qwen 72B", "desc": "HuggingFace", "provider": "huggingface"},
}


AGENT_BOTS = {
    "coordinator": {
        "emoji": "🎯",
        "name": "Координатор",
        "prompt": "Ты — Координатор команды ИИ-агентов в групповом чате.\nТы видишь сообщения других ботов и пользователя.\nТвоя задача — управлять обсуждением.\nНазначай: @Researcher1_ai_bot, @criticaibot_bot, @executorai_ai_bot\nНЕ давай [ФИНАЛЬНЫЙ ОТВЕТ] раньше шага 6.\nСначала пусть команда обсудит. Максимум 3 предложения."
    },
    "researcher": {
        "emoji": "🔍",
        "name": "Исследователь",
       "prompt": "Ты — Исследователь в команде ИИ-агентов.\nТы в групповом чате с другими ботами.\nСобирай информацию, давай факты.\nДля поиска: [SEARCH: запрос]\nНе задавай вопросы самому себе.\nНе передавай ход самому себе.\nВ конце ответа обязательно передай слово ровно одному агенту: @criticaibot_bot или @coordinator_ai_bot."
    "critic": {
        "emoji": "🧐",
        "name": "Критик",
        "prompt": "Ты — Критик в команде ИИ-агентов.\nТы видишь что пишут другие боты.\nПроверяй решения: ✅ / ⚠️ / ❌\nПредлагай улучшения. Передай @coordinator_ai_bot."
    },
    "executor": {
        "emoji": "⚡",
        "name": "Исполнитель",
        "prompt": "Ты — Исполнитель в команде ИИ-агентов.\nДелай конкретную работу: код, тексты, расчёты.\nДавай готовый результат. Передай @criticaibot_bot."
    },
}
AGENT_ROLES = AGENT_BOTS
