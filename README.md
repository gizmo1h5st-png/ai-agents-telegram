# 🤖 AI Agents Team для Telegram

Команда из 4 ИИ-агентов обсуждает и решает задачи прямо в Telegram-чате.

## ✨ Возможности

- **4 агента** с разными ролями: Координатор, Исследователь, Критик, Исполнитель
- **11 бесплатных моделей** — OpenRouter + HuggingFace
- **Переключение моделей** прямо в Telegram через `/model`
- **Автоматическое обсуждение** — агенты общаются между собой до 25 шагов
- **$0 в месяц** — бесплатные API + Railway trial

## 🚀 Быстрый старт (10 минут)

### 1. Получи API ключи

| Сервис | Ссылка |
|--------|--------|
| Telegram Bot | [@BotFather](https://t.me/BotFather) → `/newbot` |
| OpenRouter | [openrouter.ai/keys](https://openrouter.ai/keys) |
| HuggingFace | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (Fine-grained → Make calls to Inference Providers) |

### 2. Деплой на Railway

1. Форкни этот репозиторий
2. Зайди на [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Добавь **PostgreSQL**: + New → Database → PostgreSQL
4. Добавь **Redis**: + New → Database → Redis

### 3. Переменные окружения

Кликни на основной сервис → Variables → добавь:

| Variable | Value |
|----------|-------|
| `TELEGRAM_BOT_TOKEN` | Токен от BotFather |
| `OPENROUTER_API_KEY` | Ключ от OpenRouter |
| `HUGGINGFACE_API_KEY` | Ключ от HuggingFace (опционально) |
| `DATABASE_URL` | Reference из PostgreSQL |
| `REDIS_URL` | Reference из Redis |

### 4. Добавь Worker

1. В проекте: + New → GitHub Repo → тот же репозиторий
2. Кликни на новый сервис → Variables → добавь все переменные + дополнительно:

| Variable | Value |
|----------|-------|
| `APP_MODE` | `worker` |

### 5. Готово!

Напиши боту в Telegram: /task Разработай план запуска стартапа за 30 дней


## 📋 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и справка |
| `/task <текст>` | Поставить задачу команде |
| `/model` | Выбрать модель ИИ |
| `/models` | Список всех моделей |
| `/status` | Статус текущей задачи |
| `/stop` | Остановить обсуждение |

## 🤖 Агенты

| Агент | Роль |
|-------|------|
| 🎯 **Координатор** | Управляет обсуждением, распределяет задачи |
| 🔍 **Исследователь** | Собирает информацию, анализирует данные |
| 🧐 **Критик** | Проверяет решения, находит проблемы |
| ⚡ **Исполнитель** | Выполняет конкретные задачи |

## 🧠 Доступные модели

### OpenRouter (бесплатные)
- 🚀 DeepSeek V4 Flash — быстрая и стабильная
- 🟢 Nemotron 30B — reasoning от NVIDIA
- 🔺 Trinity — глубокое мышление
- 🏊 Laguna XS.2 — от Poolside
- 🦉 Owl Alpha — экспериментальная
- 🤖 GPT-OSS 120B — большая open-source
- 🤖 GPT-OSS 20B — компактная
- 💧 LFM Instruct — от Liquid AI

### HuggingFace
- 🧠 DeepSeek R1 — reasoning модель
- 🦙 Llama 3.1 8B — от Meta
- 🌟 Qwen 72B — от Alibaba

## ⚙️ Конфигурация

Дополнительные переменные (опционально):

| Variable | Default | Описание |
|----------|---------|----------|
| `MAX_STEPS_PER_TASK` | `25` | Макс. шагов обсуждения |
| `DEFAULT_MODEL` | `deepseek/deepseek-v4-flash:free` | Модель по умолчанию |
| `ALLOWED_USERS` | — | ID пользователей через запятую (если пусто — доступ всем) |

## 🏗️ Структура проекта
ai-agents-telegram/
├── app/
│ ├── main.py # FastAPI + Aiogram
│ ├── config.py # Конфигурация + список моделей
│ ├── bot/
│ │ └── handlers.py # Команды Telegram
│ ├── db/
│ │ ├── models.py # SQLAlchemy модели
│ │ ├── crud.py # Операции с БД
│ │ └── session.py # Подключение к БД
│ └── workers/
│ └── tasks.py # Celery задачи + LLM вызовы
├── run.py # Точка входа
├── requirements.txt
├── Dockerfile
├── Procfile
└── railway.toml


## 🔧 Локальная разработка

```bash
# Клонируй
git clone https://github.com/YOUR_USER/ai-agents-telegram.git
cd ai-agents-telegram

# Виртуальное окружение
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Зависимости
pip install -r requirements.txt

# Переменные (.env файл)
cp .env.example .env
# Заполни .env своими ключами

# Запуск PostgreSQL и Redis (Docker)
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:15
docker run -d -p 6379:6379 redis:7

# Запуск бота
python run.py

# Запуск воркера (в другом терминале)
APP_MODE=worker python run.py

🐛 Troubleshooting
Ошибка	Решение
ValidationError: TELEGRAM_BOT_TOKEN required	Добавь переменные в Railway
Connection refused (PostgreSQL)	Добавь DATABASE_URL из PostgreSQL
TelegramConflictError	У Worker должен быть APP_MODE=worker
RATE_LIMIT	Смени модель через /model
MODEL_LOADING	HuggingFace грузит модель, подожди 20 сек
📄 Лицензия

MIT
🙏 Благодарности

    OpenRouter — бесплатные LLM модели
    HuggingFace — Inference API
    Railway — хостинг
    Aiogram — Telegram Bot API

