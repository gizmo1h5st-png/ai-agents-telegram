# LLM Router Debug Skill

Проверять:
1. Выбранная модель соответствует provider.
2. 401/403 = проблема ключа.
3. 402/429 = лимит, включить circuit breaker.
4. 404 = модель недоступна, убрать из fallback.
5. Логировать provider/model/status без секретов.
6. Для бесплатных API снижать MAX_TOKENS и число шагов.
