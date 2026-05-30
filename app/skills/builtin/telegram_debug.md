Telegram Debug Skill

Используй, если задача связана с Telegram-ботами.

Проверочный порядок:

    Проверить, нет ли нескольких polling-инстансов на один token.
    Проверить Railway replicas = 1 для bot service.
    Проверить Redis polling lock.
    Проверить BotFather privacy mode для групп.
    Проверить usernames агентов и права писать в группу.
    Для aiogram смотреть update handled/not handled, callback errors, web_app_data.

