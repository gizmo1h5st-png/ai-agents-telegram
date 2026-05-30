# Railway Debug Skill

Проверочный порядок Railway:
1. APP_MODE=bot только у bot service.
2. Worker service выключить, если multi-bot работает через Redis pending.
3. Проверить DATABASE_URL и REDIS_URL.
4. Проверить переменные токенов всех ботов.
5. Проверить логи деплоя и runtime отдельно.
6. Если stale lock: CLEAR_POLLING_LOCK_ON_START=true один раз, затем вернуть false.
7. Не держать несколько сервисов с одинаковыми Telegram tokens.
