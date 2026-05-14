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


FREE_MODELS = {
    "deepseek": {"id": "deepseek/deepseek-v4-flash:free", "name": "🚀 DeepSeek V4", "desc": "Быстрая", "provider": "openrouter"},
    "nemotron": {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "name": "🟢 Nemotron 30B", "desc": "NVIDIA", "provider": "openrouter"},
    "trinity": {"id": "arcee-ai/trinity-large-thinking:free", "name": "🔺 Trinity", "desc": "Thinking", "provider": "openrouter"},
    "laguna": {"id": "poolside/laguna-xs.2:free", "name": "🏊 Laguna", "desc": "Poolside", "provider": "openrouter"},
    "owl": {"id": "openrouter/owl-alpha", "name": "🦉 Owl Alpha", "desc": "Эксперимент", "provider": "openrouter"},
    "gpt-120b": {"id": "openai/gpt-oss-120b:free", "name": "🤖 GPT-OSS 120B", "desc": "Большая", "provider": "openrouter"},
    "gpt-20b": {"id": "openai/gpt-oss-20b:free", "name": "🤖 GPT-OSS 20B", "desc": "Компактная", "provider": "openrouter"},
    "lfm": {"id": "liquid/lfm-2.5-1.2b-instruct:free", "name": "💧 LFM", "desc": "Liquid AI", "provider": "openrouter"},
    "hf-deepseek": {"id": "deepseek-ai/DeepSeek-R1", "name": "🧠 HF DeepSeek R1", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-llama": {"id": "meta-llama/Llama-3.1-8B-Instruct", "name": "🦙 HF Llama 3.1", "desc": "HuggingFace", "provider": "huggingface"},
    "hf-qwen": {"id": "Qwen/Qwen2.5-72B-Instruct", "name": "🌟 HF Qwen 72B", "desc": "HuggingFace", "provider": "huggingface"},
}


AGENT_ROLES = {
    "coordinator": {"emoji": "🎯", "name": "Координатор", "desc": "Управляет командой", "prompt": "Ты — Координатор команды. Управляй обсуждением.\nНазначай агентов через @имя.\nЕсли готово: [ФИНАЛЬНЫЙ ОТВЕТ] и текст.\nМаксимум 3 предложения."},
    "researcher": {"emoji": "🔍", "name": "Исследователь", "desc": "Собирает информацию", "prompt": "Ты — Исследователь. Собирай и анализируй информацию.\n2-4 пункта фактов. Передай @critic или @coordinator."},
    "critic": {"emoji": "🧐", "name": "Критик", "desc": "Проверяет решения", "prompt": "Ты — Критик. Проверяй решения команды.\nОценка: Хорошо / Замечания / Проблема\nПредлагай улучшения. Передай @coordinator."},
    "executor": {"emoji": "⚡", "name": "Исполнитель", "desc": "Выполняет задачи", "prompt": "Ты — Исполнитель. Делай конкретную работу.\nДавай готовый результат. После: @critic или @coordinator."},
    "analyst": {"emoji": "📊", "name": "Аналитик", "desc": "Данные и метрики", "prompt": "Ты — Аналитик. Работай с данными, метриками, расчётами.\nДавай конкретные цифры и выводы.\nПередай @critic."},
    "programmer": {"emoji": "💻", "name": "Программист", "desc": "Код и архитектура", "prompt": "Ты — Программист. Пиши код, проектируй архитектуру.\nОбъясняй решения. Передай @tester или @critic."},
    "copywriter": {"emoji": "✍️", "name": "Копирайтер", "desc": "Тексты и контент", "prompt": "Ты — Копирайтер. Пиши тексты, посты, статьи.\nУчитывай ЦА и tone of voice. Передай @critic."},
    "designer": {"emoji": "🎨", "name": "Дизайнер", "desc": "Визуал и UX", "prompt": "Ты — Дизайнер. Описывай визуальные концепции, UI/UX.\nДавай рекомендации по цветам, композиции. Передай @critic или @coordinator."},
    "marketer": {"emoji": "📈", "name": "Маркетолог", "desc": "Продвижение и стратегии", "prompt": "Ты — Маркетолог. Разрабатывай стратегии продвижения.\nВоронки, каналы, метрики. Передай @analyst."},
    "security": {"emoji": "🔒", "name": "Безопасник", "desc": "Риски и защита", "prompt": "Ты — Безопасник. Ищи риски и уязвимости.\nОценивай критичность. Передай @coordinator."},
    "tester": {"emoji": "🧪", "name": "Тестировщик", "desc": "QA и проверка", "prompt": "Ты — Тестировщик. Проверяй на edge cases.\nИщи баги, несоответствия. Передай @programmer или @coordinator."},
    "ideator": {"emoji": "💡", "name": "Генератор идей", "desc": "Креатив и brainstorm", "prompt": "Ты — Генератор идей. Придумывай креативные решения.\nДавай 3-5 идей, от простых до смелых. Передай @critic."},
}


TEAM_TEMPLATES = {
    "default": {"name": "🎯 Стандартная", "agents": ["coordinator", "researcher", "critic", "executor"], "desc": "Универсальная команда"},
    "startup": {"name": "🚀 Стартап", "agents": ["coordinator", "ideator", "analyst", "marketer", "critic"], "desc": "Запуск продукта"},
    "dev": {"name": "💻 Разработка", "agents": ["coordinator", "programmer", "tester", "security", "critic"], "desc": "Код и архитектура"},
    "content": {"name": "✍️ Контент", "agents": ["coordinator", "copywriter", "designer", "marketer", "critic"], "desc": "Тексты и дизайн"},
    "analysis": {"name": "📊 Аналитика", "agents": ["coordinator", "analyst", "researcher", "critic"], "desc": "Исследования и данные"},
    "creative": {"name": "💡 Креатив", "agents": ["coordinator", "ideator", "designer", "copywriter", "critic"], "desc": "Brainstorm и идеи"},
}


TASK_TEMPLATES = {
    "startup_idea": {
        "name": "🚀 Идея стартапа",
        "desc": "Найти и проработать идею стартапа",
        "text": "Придумай и проработай идею стартапа.\n\nНужно:\n1. Сформулировать идею\n2. Определить целевую аудиторию\n3. Описать проблему и решение\n4. Оценить монетизацию\n5. Предложить MVP\n6. Выделить риски\n7. Дать пошаговый план запуска",
    },
    "business_plan": {
        "name": "📊 Бизнес-план",
        "desc": "Создать краткий бизнес-план",
        "text": "Подготовь краткий бизнес-план проекта.\n\nНужно:\n1. Описать продукт\n2. Определить рынок\n3. Выделить конкурентов\n4. Предложить модель монетизации\n5. Рассчитать примерные расходы\n6. Сформулировать стратегию запуска\n7. Дать вывод по перспективности",
    },
    "marketing_strategy": {
        "name": "📈 Маркетинговая стратегия",
        "desc": "Продвижение продукта/сервиса",
        "text": "Разработай маркетинговую стратегию для продукта.\n\nНужно:\n1. Определить целевую аудиторию\n2. Сформулировать позиционирование\n3. Подобрать каналы продвижения\n4. Предложить контент-стратегию\n5. Определить ключевые метрики\n6. Составить план запуска на 30 дней",
    },
    "content_plan": {
        "name": "✍️ Контент-план",
        "desc": "Контент для соцсетей или блога",
        "text": "Создай контент-план.\n\nНужно:\n1. Определить tone of voice\n2. Предложить рубрики\n3. Дать 10 идей постов\n4. Предложить формат контента\n5. Дать рекомендации по визуалу\n6. Составить недельный план публикаций",
    },
    "landing_page": {
        "name": "🌐 Лендинг",
        "desc": "Структура продающего лендинга",
        "text": "Разработай структуру продающего лендинга.\n\nНужно:\n1. Сформулировать оффер\n2. Сделать структуру блоков\n3. Написать заголовки\n4. Предложить CTA\n5. Выделить преимущества\n6. Учесть возражения пользователей",
    },
    "technical_spec": {
        "name": "💻 ТЗ / Разработка",
        "desc": "Сформировать техзадание",
        "text": "Составь техническое задание для разработки продукта.\n\nНужно:\n1. Описать функциональность\n2. Разделить на MVP и будущие функции\n3. Описать пользовательские сценарии\n4. Предложить архитектуру\n5. Выделить риски\n6. Дать план разработки по этапам",
    },
    "code_review": {
        "name": "🧪 Code Review",
        "desc": "Проверка и улучшение кода",
        "text": "Проведи code review решения.\n\nНужно:\n1. Найти ошибки\n2. Проверить архитектуру\n3. Проверить безопасность\n4. Проверить читаемость\n5. Предложить улучшения\n6. Дать итоговую оценку",
    },
    "design_concept": {
        "name": "🎨 Дизайн-концепт",
        "desc": "UI/UX и визуальная концепция",
        "text": "Разработай дизайн-концепцию.\n\nНужно:\n1. Определить стиль\n2. Предложить цветовую палитру\n3. Описать UI-компоненты\n4. Продумать UX-сценарии\n5. Предложить визуальные референсы\n6. Дать рекомендации по адаптивности",
    },
    "market_research": {
        "name": "🔍 Исследование рынка",
        "desc": "Анализ ниши и конкурентов",
        "text": "Проведи исследование рынка.\n\nНужно:\n1. Определить размер рынка\n2. Найти основных конкурентов\n3. Описать сильные и слабые стороны конкурентов\n4. Найти свободные ниши\n5. Сформулировать возможности выхода на рынок",
    },
    "brainstorm": {
        "name": "🧠 Brainstorm",
        "desc": "Генерация идей",
        "text": "Проведи brainstorm по теме.\n\nНужно:\n1. Дать не менее 10 идей\n2. Разделить на простые / средние / смелые\n3. Выделить 3 лучшие\n4. Объяснить почему они лучшие\n5. Предложить с чего начать",
    },
}
