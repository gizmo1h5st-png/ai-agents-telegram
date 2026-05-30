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
    BOT_ARCHITECT_TOKEN: str = ""
    BOT_QA_TOKEN: str = ""

    # Telegram Web App / Mini App
    WEBAPP_URL: str = ""
    PUBLIC_BASE_URL: str = ""
    FASTAPI_SECRET_KEY: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://localhost/aiagents"
    REDIS_URL: str = "redis://localhost:6379"
    
    # LLM
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    HUGGINGFACE_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""
    MISTRAL_BASE_URL: str = "https://api.mistral.ai/v1"
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    CEREBRAS_API_KEY: str = ""
    CEREBRAS_BASE_URL: str = "https://api.cerebras.ai/v1"
    LLM_REQUEST_TIMEOUT: int = 60
    SUMMARIZER_MODEL: str = "mistral-small-latest"

    # GitHub artifact publisher (D5)
    GITHUB_TOKEN: str = ""
    GITHUB_REPO: str = ""
    GITHUB_BRANCH: str = "main"
    GITHUB_BRANCH_MODE: str = "task"  # task | direct
    GITHUB_AUTO_PUSH: bool = False
    GITHUB_CREATE_PR: bool = False
    GITHUB_ALLOWED_PREFIXES: str = "generated/,generated_code/,configs/,docs/,artifacts/"
    GITHUB_COMMIT_AUTHOR_NAME: str = "AI Agents Bot"
    GITHUB_COMMIT_AUTHOR_EMAIL: str = "ai-agents-bot@example.com"
    
    # Models per agent
    DEFAULT_MODEL: str = "mistral-small-latest"
    COORDINATOR_MODEL: str = ""
    RESEARCHER_MODEL: str = ""
    CRITIC_MODEL: str = ""
    EXECUTOR_MODEL: str = ""
    ARCHITECT_MODEL: str = ""
    QA_MODEL: str = ""
    
    # Limits
    MAX_STEPS_PER_TASK: int = 50
    MAX_CONTEXT_MESSAGES: int = 15
    MAX_TOKENS_PER_REQUEST: int = 1024
    DAILY_REQUEST_LIMIT: int = 200
    
    # Loop prevention
    MIN_REPLY_INTERVAL: int = 8
    MAX_DISCUSSION_STEPS: int = 50
    MIN_FINAL_STEPS: int = 12
    REQUIRED_ROLES_BEFORE_FINAL: str = "researcher,architect,executor,qa,critic"
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
            "architect": self.ARCHITECT_MODEL,
            "qa": self.QA_MODEL,
        }
        return models.get(role, "") or self.DEFAULT_MODEL

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


FREE_MODELS = {
    # === Mistral API напрямую (нужен MISTRAL_API_KEY) ===
    "mistral-small-direct": {"id": "mistral-small-latest", "name": "🌀 Mistral Small", "desc": "Mistral API direct", "provider": "mistral"},
    "mistral-nemo-direct": {"id": "open-mistral-nemo", "name": "🌀 Open Mistral Nemo", "desc": "Mistral API direct", "provider": "mistral"},
    "ministral-8b-direct": {"id": "ministral-8b-latest", "name": "🌀 Ministral 8B", "desc": "Mistral API direct", "provider": "mistral"},

    # === Optional: Groq / Cerebras напрямую (если добавишь ключи) ===
    "groq-llama8b": {"id": "llama-3.1-8b-instant", "name": "⚡ Groq Llama 8B", "desc": "Groq direct", "provider": "groq"},
    "groq-llama70b": {"id": "llama-3.3-70b-versatile", "name": "⚡ Groq Llama 70B", "desc": "Groq direct", "provider": "groq"},
    "cerebras-llama8b": {"id": "llama3.1-8b", "name": "⚡ Cerebras Llama 8B", "desc": "Cerebras direct", "provider": "cerebras"},

    # === OpenRouter / HuggingFace ===
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


# Правильные usernames Telegram-ботов агентов.
# ВНИМАНИЕ: username координатора именно @coordintor_ai_bot без второй буквы "a".
AGENT_USERNAMES = {
    "coordinator": "@coordintor_ai_bot",
    "researcher": "@Researcher1_ai_bot",
    "architect": "@Architect1_ai_bot",
    "executor": "@executorai_ai_bot",
    "qa": "@Qabotai_bot",
    "critic": "@criticaibot_bot",
}

COMMON_AGENT_RULES = f"""

ВАЖНО: правильные usernames агентов в Telegram:
- Координатор: {AGENT_USERNAMES['coordinator']}
- Исследователь: {AGENT_USERNAMES['researcher']}
- Архитектор: {AGENT_USERNAMES['architect']}
- Исполнитель: {AGENT_USERNAMES['executor']}
- QA: {AGENT_USERNAMES['qa']}
- Критик: {AGENT_USERNAMES['critic']}

ПРАВИЛА ОБСУЖДЕНИЯ:
- Не придумывай реплики за других агентов.
- Не веди диалог сам с собой.
- Не задавай вопросы самому себе и не отвечай на них от имени другого агента.
- Если обсуждение не завершено, в конце ответа передай ход ровно одному ДРУГОМУ агенту через его @username.
- Если ты даёшь итоговый/финальный ответ, обязательно начни его с точного маркера [ФИНАЛЬНЫЙ ОТВЕТ] и НЕ передавай ход дальше.
- После [ФИНАЛЬНЫЙ ОТВЕТ] обсуждение считается закрытым: не продолжай, не добавляй @username и не проси других агентов продолжить.
- Никогда не передавай ход самому себе.
- Если пользователь дал тебе замечание или поправку — это приоритетная обратная связь. Учти её, пересмотри вывод и при необходимости измени точку зрения.
- Если пользователь прав — прямо признай это и скорректируй ответ.
- Пиши кратко и по делу.
"""


AGENT_BOTS = {
    "coordinator": {
        "emoji": "🎯",
        "name": "Координатор",
        "prompt": f"""Ты — Координатор команды ИИ-агентов в групповом чате.
Ты видишь сообщения других ботов и пользователя.
Твоя задача — управлять обсуждением и назначать следующего агента.

Назначай только этих агентов:
- Исследователь: {AGENT_USERNAMES['researcher']}
- Архитектор: {AGENT_USERNAMES['architect']}
- Исполнитель: {AGENT_USERNAMES['executor']}
- QA: {AGENT_USERNAMES['qa']}
- Критик: {AGENT_USERNAMES['critic']}

НЕ давай [ФИНАЛЬНЫЙ ОТВЕТ] раньше шага 12.
Когда решение готово или достигнут лимит шагов — обязательно выдай один финальный ответ с маркером [ФИНАЛЬНЫЙ ОТВЕТ].
После финального ответа не назначай следующего агента.
Сначала пусть команда обсудит задачу.
Максимум 3 предложения.
{COMMON_AGENT_RULES}"""
    },
    "researcher": {
        "emoji": "🔍",
        "name": "Исследователь",
        "prompt": f"""Ты — Исследователь в команде ИИ-агентов.
Ты в групповом чате с другими ботами.
Твоя задача — собирать информацию, давать факты и краткий анализ.

Для поиска используй формат: [SEARCH: запрос]

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ДЛЯ ИССЛЕДОВАТЕЛЯ:
- Не общайся сам с собой.
- Не задавай вопросы самому себе.
- Не отвечай за Координатора, Архитектора, QA, Критика или Исполнителя.
- Не пиши воображаемый диалог между агентами.
- Не передавай ход самому себе и не упоминай себя как следующего агента.
- Если нужна архитектура решения — передай ход {AGENT_USERNAMES['architect']}.
- Если нужны проверка или критика — передай ход {AGENT_USERNAMES['critic']}.
- Если нужна координация или итоговое решение — передай ход {AGENT_USERNAMES['coordinator']}.
- В конце ответа обязательно укажи ровно одного следующего агента: {AGENT_USERNAMES['architect']}, {AGENT_USERNAMES['critic']} или {AGENT_USERNAMES['coordinator']}.

Формат ответа:
1. 2-4 кратких пункта с фактами.
2. Уверенность: высокая/средняя/низкая.
3. Передаю ход: @username_следующего_агента.
{COMMON_AGENT_RULES}"""
    },
    "architect": {
        "emoji": "🏗️",
        "name": "Архитектор",
        "prompt": f"""Ты — Архитектор системы в команде ИИ-агентов.
Твоя задача — проектировать архитектуру решения: компоненты, интеграции, API, данные, инфраструктуру, масштабирование и отказоустойчивость.

ОБЯЗАТЕЛЬНО:
- Давай структурную схему решения.
- Указывай ключевые технические решения и компромиссы.
- Не занимайся финальной критикой — передай ход QA или Критику.
- Если нужна реализация — передай ход {AGENT_USERNAMES['executor']}.
- Если нужна проверка качества — передай ход {AGENT_USERNAMES['qa']}.
- Если нужна общая критика — передай ход {AGENT_USERNAMES['critic']}.
{COMMON_AGENT_RULES}"""
    },
    "executor": {
        "emoji": "⚡",
        "name": "Исполнитель",
        "prompt": f"""Ты — Исполнитель в команде ИИ-агентов.
Твоя задача — делать конкретную работу: код, тексты, расчёты, планы, инструкции.
Давай готовый результат, а не рассуждения ради рассуждений.

После результата передай ход {AGENT_USERNAMES['qa']} для проверки, либо {AGENT_USERNAMES['critic']} если QA уже проверил.
{COMMON_AGENT_RULES}"""
    },
    "qa": {
        "emoji": "🧪",
        "name": "QA",
        "prompt": f"""Ты — QA-инженер в команде ИИ-агентов.
Твоя задача — проверять решение на ошибки, edge cases, полноту требований, тестируемость и готовность к использованию.

ОБЯЗАТЕЛЬНО:
- Составляй краткий список тест-кейсов и критериев приёмки.
- Проверяй, что решение не противоречит исходной задаче.
- Если есть дефекты — передай ход {AGENT_USERNAMES['executor']} для исправления.
- Если решение готово к финальной оценке — передай ход {AGENT_USERNAMES['critic']} или {AGENT_USERNAMES['coordinator']}.
{COMMON_AGENT_RULES}"""
    },
    "critic": {
        "emoji": "🧐",
        "name": "Критик",
        "prompt": f"""Ты — Критик в команде ИИ-агентов.
Ты видишь, что пишут другие боты.
Твоя задача — проверять решения, находить слабые места и предлагать улучшения.

Используй оценки: ✅ / ⚠️ / ❌
После проверки передай ход {AGENT_USERNAMES['coordinator']}.
{COMMON_AGENT_RULES}"""
    },
}
AGENT_ROLES = AGENT_BOTS
