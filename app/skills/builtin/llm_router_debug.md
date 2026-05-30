LLM Router Debug Skill

Проверять:

    Выбранная модель соответствует provider.
    401/403 = проблема ключа.
    402/429 = лимит, включить circuit breaker.
    404 = модель недоступна, убрать из fallback.
    Логировать provider/model/status без секретов.
    Для бесплатных API снижать MAX_TOKENS и число шагов.

