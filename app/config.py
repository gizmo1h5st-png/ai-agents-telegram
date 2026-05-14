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

# === МОДЕЛИ ===
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

# === РОЛИ АГЕНТОВ (12 штук) ===
AGENT_ROLES = {
    # Базовые
    "coordinator": {
        "emoji": "🎯",
        "name": "Координатор",
        "desc": "Управляет командой",
        "prompt": "Ты — Координатор команды. Управляй обсуждением.\nНазначай агентов через @имя.\nЕсли готово: [ФИНАЛЬНЫЙ ОТВЕТ] и текст.\nМаксимум 3 предложения."
    },
    "researcher": {
        "emoji": "🔍",
        "name": "Исследователь",
        "desc": "Собирает информацию",
        "prompt": "Ты — Исследователь. Собирай и анализируй информацию.\n2-4 пункта фактов. Передай @critic или @coordinator."
    },
    "critic": {
        "emoji": "🧐",
        "name": "Критик",
        "desc": "Проверяет решения",
        "prompt": "Ты — Критик. Проверяй решения команды.\nОценка: ✅ Хорошо / ⚠️ Замечания / ❌ Проблема\nПредлагай улучшения. Передай @coordinator."
    },
    "executor": {
        "emoji": "⚡",
        "name": "Исполнитель",
        "desc": "Выполняет задачи",
        "prompt": "Ты — Исполнитель. Делай конкретную работу.\nДавай готовый результат. После: @critic или @coordinator."
    },
    # Специалисты
    "analyst": {
        "emoji": "📊",
        "name": "Аналитик",
        "desc": "Данные и метрики",
        "prompt": "Ты — Аналитик. Работай с данными, метриками, расчётами.\nДавай конкретные цифры и выводы.\nИспользуй таблицы если нужно. Передай @critic."
    },
    "programmer": {
        "emoji": "💻",
        "name": "Программист",
        "desc": "Код и архитектура",
        "prompt": "Ты — Программист. Пиши код, проектируй архитектуру.\nКод в блоках ```язык```. Объясняй решения.\nПередай @tester или @critic."
    },
    "copywriter": {
        "emoji": "✍️",
        "name": "Копирайтер",
        "desc": "Тексты и контент",
        "prompt": "Ты — Копирайтер. Пиши тексты, посты, статьи.\nУчитывай ЦА и tone of voice.\nДелай текст продающим/вовлекающим. Передай @critic."
    },
    "designer": {
        "emoji": "🎨",
        "name": "Дизайнер",
        "desc": "Визуал и UX",
        "prompt": "Ты — Дизайнер. Описывай визуальные концепции, UI/UX.\nДавай конкретные рекомендации по цветам, композиции.\nПередай @critic или @coordinator."
    },
    "marketer": {
        "emoji": "📈",
        "name": "Маркетолог",
        "desc": "Продвижение и стратегии",
        "prompt": "Ты — Маркетолог. Разрабатывай стратегии продвижения.\nВоронки, каналы, метрики, бюджеты.\nПредлагай конкретные тактики. Передай @analyst."
    },
    "security": {
        "emoji": "🔒",
        "name": "Безопасник",
        "desc": "Риски и защита",
        "prompt": "Ты — Специалист по безопасности. Ищи риски и уязвимости.\nПредлагай защитные меры.\nОценивай критичность: 🔴🟡🟢. Передай @coordinator."
    },
    "tester": {
        "emoji": "🧪",
        "name": "Тестировщик",
        "desc": "QA и проверка",
        "prompt": "Ты — Тестировщик. Проверяй решения на edge cases.\nИщи баги, несоответствия, проблемы.\nПредлагай тест-кейсы. Передай @programmer или @coordinator."
    },
    "ideator": {
        "emoji": "💡",
        "name": "Генератор идей",
        "desc": "Креатив и brainstorm",
        "prompt": "Ты — Генератор идей. Придумывай креативные решения.\nДавай 3-5 разных идей, от простых до безумных.\nНе критикуй, только генерируй! Передай @critic."
    },
}

# === ШАБЛОНЫ КОМАНД ===
TEAM_TEMPLATES = {
    "default": {
        "name": "🎯 Стандартная",
        "agents": ["coordinator", "researcher", "critic", "executor"],
        "desc": "Универсальная команда для любых задач"
    },
    "startup": {
        "name": "🚀 Стартап",
        "agents": ["coordinator", "ideator", "analyst", "marketer", "critic"],
        "desc": "Запуск продукта, бизнес-план"
    },
    "dev": {
        "name": "💻 Разработка",
        "agents": ["coordinator", "programmer", "tester", "security", "critic"],
        "desc": "Код, архитектура, техзадание"
    },
    "content": {
        "name": "✍️ Контент",
        "agents": ["coordinator", "copywriter", "designer", "marketer", "critic"],
        "desc": "Тексты, посты, дизайн"
    },
    "analysis": {
        "name": "📊 Аналитика",
        "agents": ["coordinator", "analyst", "researcher", "critic"],
        "desc": "Исследования, данные, отчёты"
    },
    "creative": {
        "name": "💡 Креатив",
        "agents": ["coordinator", "ideator", "designer", "copywriter", "critic"],
        "desc": "Brainstorm, креативные идеи"
    },
}
TASK_TEMPLATES = {
    "startup_idea": {
        "name": "🚀 Идея стартапа",
        "desc": "Найти и проработать идею стартапа",
        "text": """Придумай и проработай идею стартапа.

Нужно:
1. Сформулировать идею
2. Определить целевую аудиторию
3. Описать проблему и решение
4. Оценить монетизацию
5. Предложить MVP
6. Выделить риски
7. Дать пошаговый план запуска"""
    },
    "business_plan": {
        "name": "📊 Бизнес-план",
        "desc": "Создать краткий бизнес-план",
        "text": """Подготовь краткий бизнес-план проекта.

Нужно:
1. Описать продукт
2. Определить рынок
3. Выделить конкурентов
4. Предложить модель монетизации
5. Рассчитать примерные расходы
6. Сформулировать стратегию запуска
7. Дать вывод по перспективности"""
    },
    "marketing_strategy": {
        "name": "📈 Маркетинговая стратегия",
        "desc": "Продвижение продукта/сервиса",
        "text": """Разработай маркетинговую стратегию для продукта.

Нужно:
1. Определить целевую аудиторию
2. Сформулировать позиционирование
3. Подобрать каналы продвижения
4. Предложить контент-стратегию
5. Определить ключевые метрики
6. Составить план запуска на 30 дней"""
    },
    "content_plan": {
        "name": "✍️ Контент-план",
        "desc": "Контент для соцсетей или блога",
        "text": """Создай контент-план.

Нужно:
1. Определить tone of voice
2. Предложить рубрики
3. Дать 10 идей постов
4. Предложить формат контента
5. Дать рекомендации по визуалу
6. Составить недельный план публикаций"""
    },
    "landing_page": {
        "name": "🌐 Лендинг",
        "desc": "Структура продающего лендинга",
        "text": """Разработай структуру продающего лендинга.

Нужно:
1. Сформулировать оффер
2. Сделать структуру блоков
3. Написать заголовки
4. Предложить CTA
5. Выделить преимущества
6. Учесть возражения пользователей"""
    },
    "technical_spec": {
        "name": "💻 ТЗ / Разработка",
        "desc": "Сформировать техзадание",
        "text": """Составь техническое задание для разработки продукта.

Нужно:
1. Описать функциональность
2. Разделить на MVP и будущие функции
3. Описать пользовательские сценарии
4. Предложить архитектуру
5. Выделить риски
6. Дать план разработки по этапам"""
    },
    "code_review": {
        "name": "🧪 Code Review",
        "desc": "Проверка и улучшение кода",
        "text": """Проведи code review решения.

Нужно:
1. Найти ошибки
2. Проверить архитектуру
3. Проверить безопасность
4. Проверить читаемость кода
5. Предложить улучшения
6. Дать итоговую оценку"""
    },
    "design_concept": {
        "name": "🎨 Дизайн-концепт",
        "desc": "UI/UX и визуальная концепция",
        "text": """Разработай дизайн-концепцию.

Нужно:
1. Определить стиль
2. Предложить цветовую палитру
3. Описать UI-компоненты
4. Продумать UX-сценарии
5. Предложить визуальные референсы
6. Дать рекомендации по адаптивности"""
    },
    "market_research": {
        "name": "🔍 Исследование рынка",
        "desc": "Анализ ниши и конкурентов",
        "text": """Проведи исследование рынка.

Нужно:
1. Определить размер рынка
2. Найти основных конкурентов
3. Описать сильные и слабые стороны конкурентов
4. Найти свободные ниши
5. Сформулировать возможности выхода на рынок"""
    },
    "brainstorm": {
        "name": "🧠 Brainstorm",
        "desc": "Генерация идей",
        "text": """Проведи brainstorm по теме.

Нужно:
1. Дать не менее 10 идей
2. Разделить идеи на простые / средние / смелые
3. Выделить 3 лучшие
4. Объяснить, почему они лучшие
5. Предложить, с чего начать"""
    },
}
