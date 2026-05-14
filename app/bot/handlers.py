from aiogram import Router, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    URLInputFile,
)
from aiogram.filters import Command, CommandStart

from app.config import (
    settings,
    FREE_MODELS,
    AGENT_ROLES,
    TEAM_TEMPLATES,
    TASK_TEMPLATES,
)
from app.db.crud import (
    create_task,
    get_active_task,
    update_task_status,
    get_chat_model,
    set_chat_model,
    get_chat_team,
    set_chat_team,
)
from app.db.models import TaskStatus
from app.workers.tasks import run_discussion_step

import logging
import urllib.parse
import html

logger = logging.getLogger(__name__)
router = Router()


def is_allowed(user_id: int) -> bool:
    if not settings.allowed_user_ids:
        return True
    return user_id in settings.allowed_user_ids


def safe(text: str) -> str:
    return html.escape(str(text or ""))


@router.message(CommandStart())
async def start_handler(message: Message):
    current_model = await get_chat_model(message.chat.id)
    current_team = await get_chat_team(message.chat.id)

    model_name = current_model.split("/")[-1].replace(":free", "")
    team_emojis = "".join([AGENT_ROLES.get(a, {}).get("emoji", "❓") for a in current_team])

    await message.answer(
        "👋 <b>Привет! Я — координатор команды ИИ-агентов.</b>\n\n"
        "🎯 <b>Команды:</b>\n"
        "• /task <i>описание</i> — поставить задачу команде\n"
        "• /templates — готовые шаблоны задач\n"
        "• /image <i>описание</i> — генерация картинки\n"
        "• /search <i>запрос</i> — поиск в интернете\n"
        "• /team — выбрать команду агентов\n"
        "• /roles — посмотреть роли агентов\n"
        "• /model — выбрать модель ИИ\n"
        "• /models — список всех моделей\n"
        "• /status — статус активной задачи\n"
        "• /stop — остановить задачу\n\n"
        f"🤖 <b>Модель:</b> <code>{safe(model_name)}</code>\n"
        f"👥 <b>Команда:</b> {team_emojis}",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def help_handler(message: Message):
    await start_handler(message)


@router.message(Command("roles"))
async def roles_handler(message: Message):
    text = "🎭 <b>Доступные роли агентов</b>\n\n"
    for key, agent in AGENT_ROLES.items():
        text += (
            f"{agent['emoji']} <b>{safe(agent['name'])}</b>\n"
            f"— {safe(agent['desc'])}\n"
            f"<code>@{safe(key)}</code>\n\n"
        )
    text += "Используй /team для выбора состава команды."
    await message.answer(text, parse_mode="HTML")


@router.message(Command("team"))
async def team_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    current_team = await get_chat_team(message.chat.id)
    current_str = "\n".join(
        [
            f"{AGENT_ROLES.get(a, {}).get('emoji', '❓')} {safe(AGENT_ROLES.get(a, {}).get('name', a))}"
            for a in current_team
        ]
    )

    buttons = []
    for key, template in TEAM_TEMPLATES.items():
        emojis = "".join([AGENT_ROLES.get(a, {}).get("emoji", "") for a in template["agents"]])
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{template['name']} {emojis}",
                    callback_data=f"team:{key}",
                )
            ]
        )

    buttons.append(
        [InlineKeyboardButton(text="🛠️ Собрать свою команду", callback_data="team:custom")]
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        f"👥 <b>Выбор команды агентов</b>\n\n"
        f"<b>Текущая команда:</b>\n{current_str}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("team:"))
async def team_callback(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    if not callback.message:
        await callback.answer()
        return

    team_key = callback.data.split(":", 1)[1]

    if team_key == "custom":
        current_team = await get_chat_team(callback.message.chat.id)

        buttons = []
        row = []
        for key, agent in AGENT_ROLES.items():
            mark = "✅ " if key in current_team else ""
            row.append(
                InlineKeyboardButton(
                    text=f"{mark}{agent['emoji']} {agent['name']}",
                    callback_data=f"agent:{key}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        buttons.append([InlineKeyboardButton(text="💾 Сохранить команду", callback_data="team:save")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        current_names = "\n".join(
            [
                f"{AGENT_ROLES[a]['emoji']} {safe(AGENT_ROLES[a]['name'])}"
                for a in current_team
                if a in AGENT_ROLES
            ]
        )

        await callback.message.edit_text(
            "🛠️ <b>Собери свою команду</b>\n"
            "Нажимай на агентов, чтобы добавить или убрать.\n"
            "<i>Минимум 2, максимум 6 агентов.</i>\n\n"
            f"<b>Сейчас выбраны:</b>\n{current_names}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if team_key == "save":
        current_team = await get_chat_team(callback.message.chat.id)
        current_names = "\n".join(
            [
                f"{AGENT_ROLES[a]['emoji']} {safe(AGENT_ROLES[a]['name'])}"
                for a in current_team
                if a in AGENT_ROLES
            ]
        )
        await callback.message.edit_text(
            f"✅ <b>Команда сохранена!</b>\n\n{current_names}",
            parse_mode="HTML",
        )
        await callback.answer("Сохранено")
        return

    if team_key not in TEAM_TEMPLATES:
        await callback.answer("❌ Шаблон команды не найден", show_alert=True)
        return

    template = TEAM_TEMPLATES[team_key]
    await set_chat_team(callback.message.chat.id, template["agents"])

    team_str = "\n".join(
        [
            f"{AGENT_ROLES[a]['emoji']} {safe(AGENT_ROLES[a]['name'])}"
            for a in template["agents"]
            if a in AGENT_ROLES
        ]
    )

    await callback.message.edit_text(
        f"✅ <b>{safe(template['name'])}</b>\n\n"
        f"{team_str}\n\n"
        f"<i>{safe(template['desc'])}</i>",
        parse_mode="HTML",
    )
    await callback.answer("Команда выбрана")


@router.callback_query(F.data.startswith("agent:"))
async def agent_toggle_callback(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    if not callback.message:
        await callback.answer()
        return

    agent_key = callback.data.split(":", 1)[1]
    current_team = await get_chat_team(callback.message.chat.id)

    if agent_key in current_team:
        if len(current_team) <= 2:
            await callback.answer("Минимум 2 агента", show_alert=True)
            return
        current_team.remove(agent_key)
        await callback.answer(f"➖ {AGENT_ROLES[agent_key]['name']}")
    else:
        if len(current_team) >= 6:
            await callback.answer("Максимум 6 агентов", show_alert=True)
            return
        current_team.append(agent_key)
        await callback.answer(f"➕ {AGENT_ROLES[agent_key]['name']}")

    await set_chat_team(callback.message.chat.id, current_team)

    buttons = []
    row = []
    for key, agent in AGENT_ROLES.items():
        mark = "✅ " if key in current_team else ""
        row.append(
            InlineKeyboardButton(
                text=f"{mark}{agent['emoji']} {agent['name']}",
                callback_data=f"agent:{key}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="💾 Сохранить команду", callback_data="team:save")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    current_names = "\n".join(
        [
            f"{AGENT_ROLES[a]['emoji']} {safe(AGENT_ROLES[a]['name'])}"
            for a in current_team
            if a in AGENT_ROLES
        ]
    )

    await callback.message.edit_text(
        "🛠️ <b>Собери свою команду</b>\n"
        "Нажимай на агентов, чтобы добавить или убрать.\n"
        "<i>Минимум 2, максимум 6 агентов.</i>\n\n"
        f"<b>Сейчас выбраны:</b>\n{current_names}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.message(Command("templates"))
async def templates_handler(message: Message):
    buttons = []
    for key, template in TASK_TEMPLATES.items():
        buttons.append(
            [
                InlineKeyboardButton(
                    text=template["name"],
                    callback_data=f"template:{key}",
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        "📚 <b>Шаблоны задач</b>\n\n"
        "Выбери шаблон — бот пришлёт готовую заготовку задачи.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("template:"))
async def template_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return

    template_key = callback.data.split(":", 1)[1]

    if template_key not in TASK_TEMPLATES:
        await callback.answer("❌ Шаблон не найден", show_alert=True)
        return

    template = TASK_TEMPLATES[template_key]
    template_text = safe(template["text"])

    await callback.message.edit_text(
        f"✅ <b>{safe(template['name'])}</b>\n"
        f"<i>{safe(template['desc'])}</i>\n\n"
        f"<b>Готовая задача:</b>\n\n"
        f"<pre>{template_text}</pre>\n\n"
        f"👉 Скопируй текст и отправь как:\n"
        f"<code>/task ...</code>",
        parse_mode="HTML",
    )
    await callback.answer("Шаблон выбран")


@router.message(Command("search", "find"))
async def search_handler(message: Message):
    query = message.text.split(maxsplit=1)
    if len(query) < 2:
        await message.answer("🔍 <code>/search запрос</code>", parse_mode="HTML")
        return

    query_text = query[1].strip()
    status_msg = await message.answer(
        f"🔍 Ищу: <i>{safe(query_text)}</i>...",
        parse_mode="HTML",
    )

    try:
        from ddgs import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query_text, max_results=5):
                title = safe(r.get("title", ""))
                body = safe(r.get("body", ""))
                link = safe(r.get("href", ""))
                results.append(f"<b>{title}</b>\n{body}\n🔗 {link}")

        if results:
            text = f"🔍 <b>Результаты: {safe(query_text)}</b>\n\n" + "\n\n".join(results[:5])
            await status_msg.edit_text(text, parse_mode="HTML")
        else:
            await status_msg.edit_text(
                f"🔍 Нет результатов: <i>{safe(query_text)}</i>",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Search error: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {safe(str(e)[:200])}", parse_mode="HTML")


@router.message(Command("image", "img"))
async def image_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    prompt = message.text.split(maxsplit=1)
    if len(prompt) < 2:
        await message.answer(
            "🖼️ <b>Генерация изображений</b>\n\n"
            "Использование:\n"
            "<code>/image красивый закат над океаном</code>",
            parse_mode="HTML",
        )
        return

    prompt_text = prompt[1].strip()
    status_msg = await message.answer("🎨 Генерирую изображение...")

    try:
        encoded_prompt = urllib.parse.quote(prompt_text)
        image_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1024&height=1024&nologo=true"
        )

        photo = URLInputFile(image_url, filename="generated.png")
        await message.answer_photo(
            photo=photo,
            caption=f"🖼️ <b>{safe(prompt_text)}</b>",
            parse_mode="HTML",
        )
        await status_msg.delete()

    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {safe(str(e)[:120])}")


@router.message(Command("model"))
async def model_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    current = await get_chat_model(message.chat.id)
    buttons = []

    for key, model in FREE_MODELS.items():
        mark = "✅ " if model["id"] == current else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}{model['name']}",
                    callback_data=f"model:{key}",
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        "🤖 <b>Выбери модель:</b>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("model:"))
async def model_callback(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    if not callback.message:
        await callback.answer()
        return

    model_key = callback.data.split(":", 1)[1]
    if model_key not in FREE_MODELS:
        await callback.answer("❌ Модель не найдена", show_alert=True)
        return

    model = FREE_MODELS[model_key]
    await set_chat_model(callback.message.chat.id, model["id"])

    await callback.message.edit_text(
        f"✅ <b>Модель изменена!</b>\n\n"
        f"<b>{safe(model['name'])}</b>\n"
        f"<i>{safe(model['desc'])}</i>\n\n"
        f"<code>{safe(model['id'])}</code>",
        parse_mode="HTML",
    )
    await callback.answer(f"Выбрана: {model['name']}")


@router.message(Command("models"))
async def models_list_handler(message: Message):
    text = "📋 <b>Доступные модели</b>\n\n"

    openrouter_models = [m for m in FREE_MODELS.values() if m.get("provider") != "huggingface"]
    hf_models = [m for m in FREE_MODELS.values() if m.get("provider") == "huggingface"]

    if openrouter_models:
        text += "<b>OpenRouter:</b>\n"
        for m in openrouter_models:
            text += f"• {safe(m['name'])} — {safe(m['desc'])}\n"

    if hf_models:
        text += "\n<b>HuggingFace:</b>\n"
        for m in hf_models:
            text += f"• {safe(m['name'])} — {safe(m['desc'])}\n"

    await message.answer(text, parse_mode="HTML")


@router.message(Command("task"))
async def task_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    task_text = message.text.replace("/task", "", 1).strip()
    if not task_text:
        await message.answer(
            "❌ Укажи задачу так:\n<code>/task описание задачи</code>",
            parse_mode="HTML",
        )
        return

    active = await get_active_task(message.chat.id)
    if active:
        await message.answer("⚠️ Уже есть активная задача. Используй /stop")
        return

    model = await get_chat_model(message.chat.id)
    team = await get_chat_team(message.chat.id)

    task = await create_task(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        description=task_text,
        model=model,
    )

    model_name = model.split("/")[-1].replace(":free", "")
    team_str = "".join([AGENT_ROLES.get(a, {}).get("emoji", "") for a in team])

    await message.answer(
        f"✅ <b>Задача #{task.id}</b>\n\n"
        f"📝 <i>{safe(task_text)}</i>\n\n"
        f"🤖 <b>Модель:</b> <code>{safe(model_name)}</code>\n"
        f"👥 <b>Команда:</b> {team_str}\n"
        "🚀 Начинаю обсуждение...",
        parse_mode="HTML",
    )

    run_discussion_step.delay(task.id)
    logger.info(f"Task {task.id} model={model} team={team}")


@router.message(Command("status"))
async def status_handler(message: Message):
    task = await get_active_task(message.chat.id)
    if not task:
        await message.answer("📭 Нет активных задач.")
        return

    status_value = getattr(task.status, "value", str(task.status))
    emoji_map = {
        "pending": "⏳",
        "in_progress": "🔄",
        "completed": "✅",
        "failed": "❌",
        "paused": "⏸",
    }
    emoji = emoji_map.get(str(status_value).lower(), "❓")

    await message.answer(
        f"📊 <b>Задача #{task.id}</b>\n\n"
        f"Статус: {emoji} {safe(status_value)}\n"
        f"Шаг: {task.current_step}/{task.max_steps}",
        parse_mode="HTML",
    )


@router.message(Command("stop"))
async def stop_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    task = await get_active_task(message.chat.id)
    if not task:
        await message.answer("📭 Нет задач для остановки.")
        return

    await update_task_status(task.id, TaskStatus.COMPLETED, "Остановлено пользователем.")
    await message.answer(f"🛑 Задача #{task.id} остановлена.")


@router.message(F.text)
async def echo_handler(message: Message):
    pass
