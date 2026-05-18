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
    MAX_STEPS_PER_TASK: int = 50
    MAX_CONTEXT_MESSAGES: int = 15
    MAX_TOKENS_PER_REQUEST: int = 1024
    DAILY_REQUEST_LIMIT: int = 200
    TOKEN_BUDGET_PER_TASK: int = 25000

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
    # === OpenRouter ===
    "deepseek-v4": {"id": "deepseek/deepseek-v4-flash:free", "name": "🚀 DeepSeek V4 Flash", "desc": "Быстрая и стабильная", "provider": "openrouter"},
    "deepseek-r1": {"id": "deepseek/deepseek-r1:free", "name": "🧠 DeepSeek R1", "desc": "Reasoning, логика", "provider": "openrouter"},
    "deepseek-chat": {"id": "deepseek/deepseek-chat-v3-0324:free", "name": "💬 DeepSeek Chat V3", "desc": "Чат, контент", "provider": "openrouter"},
    "llama4": {"id": "meta-llama/llama-4-maverick:free", "name": "🦙 Llama 4 Maverick", "desc": "1M контекст, Meta", "provider": "openrouter"},
    "qwen3": {"id": "qwen/qwen3-235b-a22b:free", "name": "🌟 Qwen3 235B", "desc": "Код и анализ", "provider": "openrouter"},
    "qwen-coder": {"id": "qwen/qwen3-coder:free", "name": "💻 Qwen3 Coder", "desc": "Для кода", "provider": "openrouter"},
    "grok-mini": {"id": "x-ai/grok-3-mini-beta:free", "name": "⚡ Grok 3 Mini", "desc": "Быстрый от xAI", "provider": "openrouter"},
    "gemma4": {"id": "google/gemma-4-31b-it:free", "name": "💎 Gemma 4 31B", "desc": "Google, эффективная", "provider": "openrouter"},
    "gemma3": {"id": "google/gemma-3-27b-it:free", "name": "💎 Gemma 3 27B", "desc": "Лёгкая от Google", "provider": "openrouter"},
    "mistral": {"id": "mistralai/mistral-small-3.1-24b-instruct:free", "name": "🌀 Mistral 24B", "desc": "Баланс скорость/качество", "provider": "openrouter"},
    "nemotron-120b": {"id": "nvidia/nemotron-3-super-120b-a12b:free", "name": "🟢 Nemotron 120B", "desc": "NVIDIA, код и reasoning", "provider": "openrouter"},
    "nemotron-30b": {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "name": "🟢 Nemotron 30B", "desc": "NVIDIA Reasoning", "provider": "openrouter"},
    "glm4": {"id": "zhipu-ai/glm-4-32b:free", "name": "🇨🇳 GLM-4 32B", "desc": "Мультиязычная", "provider": "openrouter"},
    "glm45": {"id": "z-ai/glm-4.5-air:free", "name": "🇨🇳 GLM-4.5 Air", "desc": "Агентные задачи", "provider": "openrouter"},
    "hermes": {"id": "nousresearch/hermes-3-llama-3.1-70b:free", "name": "🔮 Hermes 3 70B", "desc": "Ролевые задачи", "provider": "openrouter"},
    "gpt-120b": {"id": "openai/gpt-oss-120b:free", "name": "🤖 GPT-OSS 120B", "desc": "Большая open-source", "provider": "openrouter"},
    "gpt-20b": {"id": "openai/gpt-oss-20b:free", "name": "🤖 GPT-OSS 20B", "desc": "Компактная", "provider": "openrouter"},
    "trinity": {"id": "arcee-ai/trinity-large-thinking:free", "name": "🔺 Trinity", "desc": "Thinking", "provider": "openrouter"},
    "laguna": {"id": "poolside/laguna-xs.2:free", "name": "🏊 Laguna", "desc": "Poolside", "provider": "openrouter"},
    "owl": {"id": "openrouter/owl-alpha", "name": "🦉 Owl Alpha", "desc": "Экспериментальная", "provider": "openrouter"},
    "lfm": {"id": "liquid/lfm-2.5-1.2b-instruct:free", "name": "💧 LFM", "desc": "Liquid AI", "provider": "openrouter"},
    # === HuggingFace ===
    "hf-deepseek": {"id": "deepseek-ai/DeepSeek-R1", "name": "🧠 HF DeepSeek R1", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-llama": {"id": "meta-llama/Llama-3.1-8B-Instruct", "name": "🦙 HF Llama 3.1", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-qwen": {"id": "Qwen/Qwen2.5-72B-Instruct", "name": "🌟 HF Qwen 72B", "desc": "HuggingFace", "provider": "huggingface"},
}


AGENT_ROLES = {
    "coordinator": {"emoji": "🎯", "name": "Координатор", "desc": "Управляет командой", "prompt": "Ты — Координатор команды. Управляй обсуждением.\nНазначай агентов через @имя.\nНЕ давай [ФИНАЛЬНЫЙ ОТВЕТ] слишком рано!\nСначала пусть вся команда выскажется.\nТолько когда собраны все данные, критик проверил, и решение полное — тогда пиши [ФИНАЛЬНЫЙ ОТВЕТ].\nМаксимум 3 предложения на реплику."},
    "researcher": {"emoji": "🔍", "name": "Исследователь", "desc": "Собирает информацию", "prompt": "Ты — Исследователь. Собирай информацию.\n2-4 пункта фактов. Для поиска: [SEARCH: запрос]\nДля запоминания: [REMEMBER: факт]\nПередай @critic или @coordinator."},
    "critic": {"emoji": "🧐", "name": "Критик", "desc": "Проверяет решения", "prompt": "Ты — Критик. Проверяй решения.\nОценка: ✅ / ⚠️ / ❌\nПредлагай улучшения. Передай @coordinator.\nНе спеши одобрять — проверь тщательно."},
    "executor": {"emoji": "⚡", "name": "Исполнитель", "desc": "Выполняет задачи", "prompt": "Ты — Исполнитель. Делай конкретную работу.\nДавай готовый результат. После: @critic или @coordinator."},
    "analyst": {"emoji": "📊", "name": "Аналитик", "desc": "Данные и метрики", "prompt": "Ты — Аналитик. Работай с данными и метриками.\nДавай конкретные цифры и выводы. Передай @critic."},
    "programmer": {"emoji": "💻", "name": "Программист", "desc": "Код и архитектура", "prompt": "Ты — Программист. Пиши код и проектируй архитектуру.\nОбъясняй решения. Передай @tester или @critic."},
    "copywriter": {"emoji": "✍️", "name": "Копирайтер", "desc": "Тексты и контент", "prompt": "Ты — Копирайтер. Пиши тексты и посты.\nУчитывай ЦА и tone of voice. Передай @critic."},
    "designer": {"emoji": "🎨", "name": "Дизайнер", "desc": "Визуал и UX", "prompt": "Ты — Дизайнер. Описывай визуальные концепции и UI/UX.\nДавай рекомендации по цветам и композиции. Передай @critic."},
    "marketer": {"emoji": "📈", "name": "Маркетолог", "desc": "Продвижение", "prompt": "Ты — Маркетолог. Разрабатывай стратегии продвижения.\nВоронки, каналы, метрики. Передай @analyst."},
    "security": {"emoji": "🔒", "name": "Безопасник", "desc": "Риски и защита", "prompt": "Ты — Безопасник. Ищи риски и уязвимости.\nОценивай критичность. Передай @coordinator."},
    "tester": {"emoji": "🧪", "name": "Тестировщик", "desc": "QA и проверка", "prompt": "Ты — Тестировщик. Проверяй на edge cases.\nИщи баги. Передай @programmer или @coordinator."},
    "ideator": {"emoji": "💡", "name": "Генератор идей", "desc": "Креатив", "prompt": "Ты — Генератор идей. Придумывай креативные решения.\nДавай 3-5 идей. Передай @critic."},
}


TEAM_TEMPLATES = {
    "default": {"name": "🎯 Стандартная", "agents": ["coordinator", "researcher", "critic", "executor"], "desc": "Универсальная"},
    "startup": {"name": "🚀 Стартап", "agents": ["coordinator", "ideator", "analyst", "marketer", "critic"], "desc": "Запуск продукта"},
    "dev": {"name": "💻 Разработка", "agents": ["coordinator", "programmer", "tester", "security", "critic"], "desc": "Код и архитектура"},
    "content": {"name": "✍️ Контент", "agents": ["coordinator", "copywriter", "designer", "marketer", "critic"], "desc": "Тексты и дизайн"},
    "analysis": {"name": "📊 Аналитика", "agents": ["coordinator", "analyst", "researcher", "critic"], "desc": "Исследования"},
    "creative": {"name": "💡 Креатив", "agents": ["coordinator", "ideator", "designer", "copywriter", "critic"], "desc": "Brainstorm"},
}


TASK_TEMPLATES = {
    "startup_idea": {"name": "🚀 Идея стартапа", "desc": "Найти и проработать идею", "text": "Придумай и проработай идею стартапа.\n\n1. Сформулировать идею\n2. Определить ЦА\n3. Описать проблему и решение\n4. Оценить монетизацию\n5. Предложить MVP\n6. Выделить риски\n7. Пошаговый план запуска"},
    "business_plan": {"name": "📊 Бизнес-план", "desc": "Краткий бизнес-план", "text": "Подготовь краткий бизнес-план.\n\n1. Описать продукт\n2. Определить рынок\n3. Выделить конкурентов\n4. Модель монетизации\n5. Примерные расходы\n6. Стратегия запуска"},
    "marketing_strategy": {"name": "📈 Маркетинг", "desc": "Стратегия продвижения", "text": "Разработай маркетинговую стратегию.\n\n1. Целевая аудитория\n2. Позиционирование\n3. Каналы продвижения\n4. Контент-стратегия\n5. Ключевые метрики\n6. План на 30 дней"},
    "content_plan": {"name": "✍️ Контент-план", "desc": "Контент для соцсетей", "text": "Создай контент-план.\n\n1. Tone of voice\n2. Рубрики\n3. 10 идей постов\n4. Формат контента\n5. Визуал\n6. Недельный план"},
    "technical_spec": {"name": "💻 ТЗ", "desc": "Техзадание", "text": "Составь ТЗ для разработки.\n\n1. Функциональность\n2. MVP vs будущее\n3. Сценарии\n4. Архитектура\n5. Риски\n6. План по этапам"},
    "brainstorm": {"name": "🧠 Brainstorm", "desc": "Генерация идей", "text": "Brainstorm по теме.\n\n1. Минимум 10 идей\n2. Простые / средние / смелые\n3. Топ 3\n4. Почему они лучшие\n5. С чего начать"},
    "code_review": {"name": "🧪 Code Review", "desc": "Проверка кода", "text": "Проведи code review.\n\n1. Ошибки\n2. Архитектура\n3. Безопасность\n4. Читаемость\n5. Улучшения\n6. Итоговая оценка"},
    "landing_page": {"name": "🌐 Лендинг", "desc": "Структура лендинга", "text": "Разработай структуру лендинга.\n\n1. Оффер\n2. Структура блоков\n3. Заголовки\n4. CTA\n5. Преимущества\n6. Возражения"},
    "market_research": {"name": "🔍 Исследование рынка", "desc": "Анализ ниши", "text": "Исследование рынка.\n\n1. Размер рынка\n2. Конкуренты\n3. Их сильные/слабые стороны\n4. Свободные ниши\n5. Возможности выхода"},
    "design_concept": {"name": "🎨 Дизайн-концепт", "desc": "UI/UX концепция", "text": "Дизайн-концепция.\n\n1. Стиль\n2. Цветовая палитра\n3. UI-компоненты\n4. UX-сценарии\n5. Референсы\n6. Адаптивность"},
}
