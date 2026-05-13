# 🤖 AI Agents Team для Telegram

Команда из 4 AI-агентов обсуждает задачи прямо в Telegram-чате.

## Быстрый старт (5 минут)

### 1. Получи токены
- **Telegram Bot**: [@BotFather](https://t.me/BotFather) → /newbot
- **OpenRouter API**: [openrouter.ai/keys](https://openrouter.ai/keys) (бесплатно)

### 2. Деплой на Railway
1. Форкни этот репозиторий
2. Зайди на [railway.app](https://railway.app)
3. New Project → Deploy from GitHub → выбери репозиторий
4. Добавь сервисы: **PostgreSQL** и **Redis** (кнопка + New)
5. В Settings основного сервиса добавь переменные:
   - `TELEGRAM_BOT_TOKEN` = твой токен бота
   - `OPENROUTER_API_KEY` = твой API ключ

### 3. Добавь воркер
1. В том же проекте: + New → Empty Service
2. Source: тот же GitHub репо
3. Settings → Start Command: `celery -A app.workers.tasks worker -l info`
4. Переменные скопируются автоматически

### 4. Готово!
Напиши боту: `/task Напиши план запуска стартапа`

## Команды бота
- `/start` — приветствие
- `/task <текст>` — поставить задачу
- `/status` — статус текущей задачи
- `/stop` — остановить обсуждение

## Агенты
- 🎯 **Координатор** — управляет обсуждением
- 🔍 **Исследователь** — ищет информацию
- 🧐 **Критик** — проверяет решения
- ⚡ **Исполнитель** — выполняет задачи

## Стоимость
**$0** — используются бесплатные модели OpenRouter + trial Railway ($5 хватит на месяцы)
