# 🤖 AI Agents Telegram Team

Мультиагентная команда ИИ-ботов для Telegram-группы. Боты обсуждают задачи, распределяют роли, генерируют тексты/код/файлы, проверяют результат, сохраняют историю, используют память, skills, LLM fallback router и могут пушить подготовленные артефакты в GitHub.

Проект рассчитан на деплой в Railway с Redis и PostgreSQL.

---

## ✨ Что умеет проект

- **6 Telegram-агентов** в одной группе:
  - 🎯 Координатор
  - 🔍 Исследователь
  - 🏗️ Архитектор
  - ⚡ Исполнитель
  - 🧪 QA
  - 🧐 Критик
- **Выбор команды агентов** под задачу: все 6, базовая команда, тех-команда, быстрый режим.
- **Настраиваемые промпты** для каждого агента: до 5 стилей поведения на роль.
- **Dynamic Steps** — автоматический выбор типа задачи, команды и лимита шагов.
- **LLM Router** — Mistral direct + OpenRouter/HuggingFace fallback, circuit breaker и cache.
- **Память проекта** — `/remember`, `/memory`, `/memory_search`, lessons после задач.
- **Skills & Context** — markdown-навыки и постоянный контекст проекта подмешиваются в prompt.
- **Run Journal** — `/plan`, `/events`, трассировка хода задачи.
- **GitHub artifacts** — агенты могут создавать файлы в формате `[FILE: path]`, а бот пушит их в GitHub через PyGithub.
- **Защита от Telegram polling conflict** через Redis polling lock.
- **Разбиение длинных сообщений** на несколько Telegram-сообщений.

---

## 🧩 Архитектура

```text
Telegram Group
   ↓
6 Telegram Bots / Aiogram polling
   ↓
Redis
   ├─ active_task
   ├─ pending:{chat_id}:{role}
   ├─ turn
   ├─ history
   ├─ task_team
   ├─ task_skills
   ├─ artifacts
   └─ run_events
   ↓
LLM Router
   ├─ Mistral API
   ├─ OpenRouter
   ├─ HuggingFace
   ├─ Groq optional
   └─ Cerebras optional
   ↓
PostgreSQL
   ├─ tasks
   ├─ messages
   ├─ agent_memory
   └─ token_usage_log
   ↓
GitHub API / PyGithub
   └─ commit generated artifacts
```

---

## 🤖 Агенты

| Агент | Username | Назначение |
|---|---|---|
| 🎯 Координатор | `@coordintor_ai_bot` | Управляет маршрутом обсуждения, следит за финализацией |
| 🔍 Исследователь | `@Researcher1_ai_bot` | Собирает факты, контекст, ограничения |
| 🏗️ Архитектор | `@Architect1_ai_bot` | Проектирует архитектуру, компоненты, API, инфраструктуру |
| ⚡ Исполнитель | `@executorai_ai_bot` | Делает практический результат: код, файлы, планы |
| 🧪 QA | `@Qabotai_bot` | Проверяет результат, edge cases, тест-кейсы |
| 🧐 Критик | `@criticaibot_bot` | Ищет слабые места, риски, противоречия |

---

## 🚀 Быстрый старт на Railway

### 1. Подготовить Telegram-ботов

Создай ботов через [@BotFather](https://t.me/BotFather):

```text
BOT_COORDINATOR_TOKEN
BOT_RESEARCHER_TOKEN
BOT_ARCHITECT_TOKEN
BOT_EXECUTOR_TOKEN
BOT_QA_TOKEN
BOT_CRITIC_TOKEN
```

Для групп желательно отключить Privacy Mode:

```text
/setprivacy → Disable
```

---

### 2. Создать Railway Project

Добавь сервисы:

```text
Bot service
PostgreSQL
Redis
```

Celery worker сейчас не обязателен для multi-bot режима: агенты работают через Redis pending loop внутри `app/multibot/engine.py`.

---

### 3. Railway Variables

Минимальный набор:

```env
APP_MODE=bot

BOT_COORDINATOR_TOKEN=...
BOT_RESEARCHER_TOKEN=...
BOT_ARCHITECT_TOKEN=...
BOT_EXECUTOR_TOKEN=...
BOT_QA_TOKEN=...
BOT_CRITIC_TOKEN=...

DATABASE_URL=...
REDIS_URL=...

MISTRAL_API_KEY=...
MISTRAL_BASE_URL=https://api.mistral.ai/v1

DEFAULT_MODEL=mistral-small-latest
COORDINATOR_MODEL=mistral-small-latest
RESEARCHER_MODEL=open-mistral-nemo
ARCHITECT_MODEL=mistral-small-latest
EXECUTOR_MODEL=mistral-small-latest
QA_MODEL=ministral-8b-latest
CRITIC_MODEL=ministral-8b-latest

MAX_DISCUSSION_STEPS=24
MIN_FINAL_STEPS=12
REQUIRED_ROLES_BEFORE_FINAL=researcher,architect,executor,qa,critic
MAX_TOKENS_PER_REQUEST=1500
LLM_CONTINUE_MAX=2
MIN_REPLY_INTERVAL=8

DYNAMIC_STEPS_ENABLED=true
AUTO_TEAM_BY_TASK_TYPE=true
DEFAULT_TASK_TYPE=general

POLLING_LOCK_TTL=45
POLLING_LOCK_WAIT=180
CLEAR_POLLING_LOCK_ON_START=false
```

Рекомендуется ограничить доступ:

```env
ALLOWED_USERS=123456789
```

---

## 🧠 Dynamic Steps

Система классифицирует задачу и подбирает команду/лимиты:

| Тип | Команда | Бюджет |
|---|---|---|
| `simple_artifact` | Coordinator → Executor → QA | min=3, soft=5, hard=8 |
| `simple_answer` | Coordinator → Executor → Critic | min=2, soft=4, hard=6 |
| `general` | Coordinator → Researcher → Executor → Critic | min=5, soft=10, hard=16 |
| `research` | Coordinator → Researcher → Critic | min=6, soft=12, hard=18 |
| `debug` | Coordinator → Researcher → Executor → QA → Critic | min=6, soft=12, hard=20 |
| `architecture` | Все 6 агентов | min=10, soft=18, hard=28 |

Для простых файловых задач команда автоматически сокращается, чтобы агенты не спорили 20 шагов о `hello.html`.

---

## 📋 Основные команды

| Команда | Описание |
|---|---|
| `/start` | Главное меню |
| `/team` | Выбор состава агентов |
| `/prompts` | Стиль промпта каждого агента |
| `/model` | Общая модель |
| `/agentmodel` | Модель по каждому агенту |
| `/steps` | Ручной лимит шагов |
| `/delay` | Задержка между агентами |
| `/status` | Статус активной задачи |
| `/plan` | План текущей задачи |
| `/events` | Журнал событий задачи |
| `/history` | История задач |
| `/memory` | Память проекта |
| `/remember текст` | Запомнить факт |
| `/memory_search запрос` | Поиск в памяти |
| `/skills` | Управление skills |
| `/context` | Показать постоянный контекст |
| `/artifacts` | Показать файлы, подготовленные для GitHub |
| `/push` | Запушить artifacts в GitHub |
| `/github` | Статус GitHub publisher |
| `/finalize` | Принудительная финализация |
| `/cleanup` | Очистить runtime-состояние Redis |
| `/stop` | Остановить задачу |

---

## 👥 Команды агентов

В `/team` доступны пресеты:

| Пресет | Состав |
|---|---|
| 👑 Все 6 | Coordinator, Researcher, Architect, Executor, QA, Critic |
| 🧩 База 4 | Coordinator, Researcher, Executor, Critic |
| 🛠 Тех-команда | Все 6, для технических задач |
| ⚡ Быстро | Coordinator, Executor, Critic |

---

## 📝 Промпты агентов

Через `/prompts` можно выбрать стиль.

Примеры:

```text
Coordinator → 🧠 Глубокий / ⚡ Быстрый / 🧩 Строгий
Researcher → 🔬 Deep research / 🛠 Техфакты
Architect → 🏢 Enterprise / 🚀 MVP / ☁️ Cloud
Executor → 💻 Код / 📋 План / ⚙️ DevOps
QA → 🧪 Строгий QA / ✅ Тест-кейсы
Critic → 🔥 Жёсткий / 🏁 Финальная проверка
```

---

## 🧩 Skills & Context

Папка skills:

```text
app/skills/builtin/
```

Встроенные skills:

```text
telegram_debug.md
railway_debug.md
architecture_review.md
qa_checklist.md
llm_router_debug.md
github_artifacts.md
```

Постоянный контекст:

```text
app/context/PROJECT.md
app/context/AGENTS.md
app/context/DEPLOYMENT.md
```

Команды:

```text
/skills
/context
```

---

## 🧠 Memory & Learning

Память хранится в PostgreSQL `agent_memory`.

```text
/remember Проект использует Railway, Redis polling lock и PostgreSQL history.
/memory
/memory_search Railway
/forget
```

После завершения задачи сохраняется lesson.

---

## 📦 GitHub Artifacts & Push

Агенты могут подготовить файл для GitHub в формате:

````text
[FILE: generated_code/hello.html]
```html
<!DOCTYPE html>
<html>
<head>
<title>Hello World</title>
</head>
<body>
<h1>Hello World</h1>
</body>
</html>
```
````

Проверить artifacts:

```text
/artifacts
```

Запушить:

```text
/push
```

---

## GitHub Variables

```env
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=gizmo1h5st-png/ai-agents-telegram
GITHUB_BRANCH=main
GITHUB_BRANCH_MODE=direct
GITHUB_AUTO_PUSH=false
GITHUB_CREATE_PR=false
GITHUB_ALLOWED_PREFIXES=generated/,generated_code/,configs/,docs/,artifacts/
```

Безопасный режим:

```env
GITHUB_BRANCH_MODE=task
GITHUB_CREATE_PR=true
GITHUB_AUTO_PUSH=false
```

Прямой пуш в main:

```env
GITHUB_BRANCH_MODE=direct
GITHUB_BRANCH=main
GITHUB_AUTO_PUSH=false
```

Автопуш включать только после тестов:

```env
GITHUB_AUTO_PUSH=true
```

---

## 🔐 Безопасность

- GitHub token хранится только в Railway Variables.
- Токены не логируются.
- Для GitHub artifacts разрешены только безопасные директории.
- `.env`, ключи, credentials и private keys запрещены.
- `/cleanup`, `/push`, `/forget` лучше ограничить через `ALLOWED_USERS`.
- Bot service должен иметь `replicas=1`.

---

## 🛠 Локальный запуск

```bash
git clone https://github.com/gizmo1h5st-png/ai-agents-telegram.git
cd ai-agents-telegram

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python run.py
```

---

## 🩺 Troubleshooting

### TelegramConflictError

Проверь:

```text
replicas=1
нет второго bot service с теми же токенами
POLLING_LOCK_TTL/POLLING_LOCK_WAIT
CLEAR_POLLING_LOCK_ON_START=true только аварийно один раз
```

### LLM 402/429

```text
лимит провайдера
снизить MAX_TOKENS_PER_REQUEST
уменьшить число шагов
проверить /providers
```

### LLM режет ответ

Используется continuation:

```env
LLM_CONTINUE_MAX=2
MAX_TOKENS_PER_REQUEST=1500
```

### `/artifacts` пустой

Агент должен использовать строгий формат:

````text
[FILE: path]
```lang
content
```
````

### GitHub push не работает

Проверь:

```text
/github
GITHUB_TOKEN
GITHUB_REPO=owner/repo
GITHUB_ALLOWED_PREFIXES
```

---

## 📁 Структура проекта

```text
app/
  main.py
  config.py
  multibot/
    engine.py
  llm/
    router.py
  db/
    models.py
    session.py
    crud.py
  skills/
    loader.py
    builtin/
  context/
  memory/
    service.py
  artifacts.py
  github_publisher.py
  github_service.py
  run_journal.py
  workers/
    tasks.py

Dockerfile
requirements.txt
run.py
.env.example
```

---

## 🧪 Рекомендуемый smoke test

```text
Задача: создай простую HTML страницу hello world и подготовь файл для GitHub в generated_code/hello.html
```

Ожидаемо:

```text
Тип: simple_artifact
Команда: 🎯 → ⚡ → 🧪
/artifacts показывает generated_code/hello.html
/push отправляет файл в GitHub
```

---

## 📄 Лицензия

MIT
