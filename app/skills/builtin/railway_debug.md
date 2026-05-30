Railway Debug Skill

Проверочный порядок Railway:

    APP_MODE=bot только у bot service.
    Worker service выключить, если multi-bot работает через Redis pending.
    Проверить DATABASE_URL и REDIS_URL.
    Проверить переменные токенов всех ботов.
    Проверить логи деплоя и runtime отдельно.
    Если stale lock: CLEAR_POLLING_LOCK_ON_START=true один раз, затем вернуть false.
    Не держать несколько сервисов с одинаковыми Telegram tokens.

