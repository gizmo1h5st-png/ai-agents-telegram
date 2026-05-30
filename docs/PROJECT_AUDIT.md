# Project Audit

## Executive Summary
Проект — мультиагентный Telegram-бот с Redis, PostgreSQL, LLM Router, Skills, Memory и GitHub artifacts.

## Current State
- 6 Telegram-агентов.
- Railway deploy.
- Redis state/polling lock.
- PostgreSQL history/memory.
- GitHub artifact push.

## Recommendations
- Использовать Dynamic Steps.
- Для файловых задач требовать artifact-first.
- Проверять /artifacts перед /push.

## P0/P1/P2 Roadmap
### P0
1. Настройка TTL для Redis lock (`telegram_polling_lock`).
2. Внедрение логирования для `/push` и `/memory`.
3. Проверка Telegram privacy mode.

### P1
1. Автоматизированное тестирование интеграций Redis+PostgreSQL.
2. Резервное копирование PostgreSQL.
3. Проверка утечек токенов в логах (GITHUB_TOKEN, TELEGRAM_TOKEN, LLM_API_KEY).

### P2
1. Оптимизация LLM Router под нагрузку.
2. Внедрение кэширования для частых запросов.

## Acceptance Criteria
### Безопасность
- Отсутствие чувствительных данных в публичных логах.

### Надёжность
- Redis lock не вызывает дедлоки.
- PostgreSQL не теряет данные при рестарте.

### Масштабируемость
- GitHub Artifacts генерируются корректно перед `/push`.
