# Deployment (Railway)

**Обязательные переменные:**
- BOT_COORDINATOR_TOKEN, BOT_RESEARCHER_TOKEN, BOT_ARCHITECT_TOKEN, BOT_EXECUTOR_TOKEN, BOT_QA_TOKEN, BOT_CRITIC_TOKEN
- DATABASE_URL (PostgreSQL)
- REDIS_URL
- MISTRAL_API_KEY (или OPENROUTER_API_KEY)

**Рекомендуемые настройки:**
- replicas=1 (обязательно, иначе polling conflict)
- POLLING_LOCK_TTL=45
- POLLING_LOCK_WAIT=180
- CLEAR_POLLING_LOCK_ON_START=false (только в аварийных случаях)

**Полезные команды в чате:**
- /cleanup — очистить runtime Redis
- /status, /plan, /events
- /finalize — принудительная финализация

**GitHub интеграция:**
- GITHUB_TOKEN, GITHUB_REPO
- GITHUB_AUTO_PUSH=false (включай только после тестов)