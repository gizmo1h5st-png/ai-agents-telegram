# Telegram Debug Skill

Используй, если задача связана с Telegram-ботами.

Проверочный порядок:
1. Проверить, нет ли нескольких polling-инстансов на один token.
2. Проверить Railway replicas = 1 для bot service.
3. Проверить Redis polling lock.
4. Проверить BotFather privacy mode для групп.
5. Проверить usernames агентов и права писать в группу.
6. Для aiogram смотреть update handled/not handled, callback errors, web_app_data.
