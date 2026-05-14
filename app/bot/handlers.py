from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, URLInputFile
from aiogram.filters import Command, CommandStart
from app.config import settings, FREE_MODELS, AGENT_ROLES, TEAM_TEMPLATES, TASK_TEMPLATES
from app.db.crud import create_task, get_active_task, update_task_status, get_chat_model, set_chat_model, get_chat_team, set_chat_team
from app.db.models import TaskStatus
from app.workers.tasks import run_discussion_step
import logging
import urllib.parse

logger = logging.getLogger(__name__)
router = Router()

def is_allowed(user_id: int) -> bool:
    if not settings.allowed_user_ids:
        return True
    return user_id in settings.allowed_user_ids

@router.message(CommandStart())
async def start_handler(message: Message):
    current_model = await get_chat_model(message.chat.id)
    current_team = await get_chat_team(message.chat.id)
    model_name = current_model.split("/")[-1].replace(":free", "")
    team_emojis = "".join([AGENT_ROLES.get(a, {}).get("emoji", "?") for a in current_team])
    
    await message.answer(
        "👋 <b>Привет! Я — координатор команды ИИ-агентов.</b>\n\n"
        "🎯 <b>Команды:</b>\n"
        "• /task <i>описание</i> — задача для команды\n"
        "• /templates — готовые шаблоны задач\n"
        "• /image <i>описание</i> — генерация картинки\n"
        "• /search <i>запрос</i> — поиск в интернете\n"
        "• /team — выбрать команду агентов\n"
        "• /roles — все доступные роли\n"
        "• /model — выбрать модель ИИ\n"
        "• /status — статус задачи\n"
        "• /stop — остановить\n\n"
        f"🤖 Модель: <code>{model_name}</code>\n"
        f"👥 Команда: {team_emojis}",
        parse_mode="HTML"
    )

@router.message(Command("roles"))
async def roles_handler(message: Message):
    text = "🎭 <b>Все агенты:</b>\n\n"
    for key, agent in AGENT_ROLES.items():
        text += f"{agent['emoji']} <b>{agent['name']}</b> — {agent['desc']}\n"
    text += "\n/team — выбрать команду"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("team"))
async def team_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    
    current_team = await get_chat_team(message.chat.id)
    current_str = " ".join([AGENT_ROLES.get(a, {}).get("emoji", "?") + " " + AGENT_ROLES.get(a, {}).get("name", a) for a in current_team])
    
    buttons = []
    for key, template in TEAM_TEMPLATES.items():
        emojis = "".join([AGENT_ROLES.get(a, {}).get("emoji", "") for a in template["agents"]])
        buttons.append([InlineKeyboardButton(text=f"{template['name']} {emojis}", callback_data=f"team:{key}")])
    
    buttons.append([InlineKeyboardButton(text="🛠️ Собрать свою", callback_data="team:custom")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await message.answer(
        f"👥 <b>Команда агентов</b>\n\nСейчас: {current_str}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("team:"))
async def team_callback(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    
    team_key = callback.data.split(":")[1]
    
    if team_key == "custom":
        current_team = await get_chat_team(callback.message.chat.id)
        buttons = []
        row = []
        for key, agent in AGENT_ROLES.items():
            mark = "✅" if key in current_team else ""
            row.append(InlineKeyboardButton(text=f"{mark}{agent['emoji']}", callback_data=f"agent:{key}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton(text="💾 Сохранить", callback_data="team:save")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        team_names = "\n".join([f"{AGENT_ROLES[a]['emoji']} {AGENT_ROLES[a]['name']}" for a in current_team if a in AGENT_ROLES])
        await callback.message.edit_text(
            f"🛠️ <b>Собери команду</b> (2-6 агентов)\n\nСейчас:\n{team_names}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
    
    if team_key == "save":
        current_team = await get_chat_team(callback.message.chat.id)
        team_str = "\n".join([f"{AGENT_ROLES[a]['emoji']} {AGENT_ROLES[a]['name']}" for a in current_team if a in AGENT_ROLES])
        await callback.message.edit_text(f"✅ <b>Команда сохранена!</b>\n\n{team_str}", parse_mode="HTML")
        await callback.answer("Сохранено!")
        return
    
    if team_key in TEAM_TEMPLATES:
        template = TEAM_TEMPLATES[team_key]
        await set_chat_team(callback.message.chat.id, template["agents"])
        team_str = "\n".join([f"{AGENT_ROLES[a]['emoji']} {AGENT_ROLES[a]['name']}" for a in template["agents"]])
        await callback.message.edit_text(
            f"✅ <b>{template['name']}</b>\n\n{team_str}\n\n<i>{template['desc']}</i>",
            parse_mode="HTML"
        )
        await callback.answer("Готово!")

@router.callback_query(F.data.startswith("agent:"))
async def agent_toggle_callback(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    
    agent_key = callback.data.split(":")[1]
    current_team = await get_chat_team(callback.message.chat.id)
    
    if agent_key in current_team:
        if len(current_team) <= 2:
            await callback.answer("Минимум 2 агента!", show_alert=True)
            return
        current_team.remove(agent_key)
        await callback.answer(f"➖ {AGENT_ROLES[agent_key]['name']}")
    else:
        if len(current_team) >= 6:
            await callback.answer("Максимум 6!", show_alert=True)
            return
        current_team.append(agent_key)
        await callback.answer(f"➕ {AGENT_ROLES[agent_key]['name']}")
    
    await set_chat_team(callback.message.chat.id, current_team)
    
    buttons = []
    row = []
    for key, agent in AGENT_ROLES.items():
        mark = "✅" if key in current_team else ""
        row.append(InlineKeyboardButton(text=f"{mark}{agent['emoji']}", callback_data=f"agent:{key}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="💾 Сохранить", callback_data="team:save")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    team_names = "\n".join([f"{AGENT_ROLES[a]['emoji']} {AGENT_ROLES[a]['name']}" for a in current_team if a in AGENT_ROLES])
    await callback.message.edit_text(
        f"🛠️ <b>Собери команду</b> (2-6 агентов)\n\nСейчас:\n{team_names}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
@router.message(Command("search", "find"))
async def search_handler(message: Message):
    query = message.text.split(maxsplit=1)
    if len(query) < 2:
        await message.answer("🔍 <code>/search запрос</code>", parse_mode="HTML")
        return
    
    query_text = query[1].strip()
    status_msg = await message.answer(f"🔍 Ищу: <i>{query_text}</i>...", parse_mode="HTML")
    
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query_text, max_results=5):
                title = r.get("title", "")
                body = r.get("body", "")
                link = r.get("href", "")
                results.append(f"<b>{title}</b>\n{body}\n🔗 {link}")
        
        if results:
            text = f"🔍 <b>Результаты: {query_text}</b>\n\n"
            text += "\n\n".join(results[:5])
            await status_msg.edit_text(text, parse_mode="HTML")
        else:
            await status_msg.edit_text(f"🔍 Нет результатов: <i>{query_text}</i>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Search error: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:200]}")

        
@router.message(Command("image", "img"))
async def image_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    
    prompt = message.text.split(maxsplit=1)
    if len(prompt) < 2:
        await message.answer("🖼️ <code>/image описание картинки</code>", parse_mode="HTML")
        return
    
    prompt_text = prompt[1].strip()
    status_msg = await message.answer("🎨 Генерирую...")
    
    try:
        encoded_prompt = urllib.parse.quote(prompt_text)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        photo = URLInputFile(image_url, filename="generated.png")
        await message.answer_photo(photo=photo, caption=f"🖼️ <b>{prompt_text}</b>", parse_mode="HTML")
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

@router.message(Command("model"))
async def model_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    
    current = await get_chat_model(message.chat.id)
    buttons = []
    for key, model in FREE_MODELS.items():
        mark = "✅ " if model["id"] == current else ""
        buttons.append([InlineKeyboardButton(text=f"{mark}{model['name']}", callback_data=f"model:{key}")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🤖 <b>Выбери модель:</b>", reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("model:"))
async def model_callback(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    
    model_key = callback.data.split(":")[1]
    if model_key not in FREE_MODELS:
        await callback.answer("❌", show_alert=True)
        return
    
    model = FREE_MODELS[model_key]
    await set_chat_model(callback.message.chat.id, model["id"])
    await callback.message.edit_text(f"✅ <b>{model['name']}</b>\n<code>{model['id']}</code>", parse_mode="HTML")
    await callback.answer(f"Выбрана: {model['name']}")

@router.message(Command("models"))
async def models_list_handler(message: Message):
    text = "📋 <b>Модели:</b>\n\n"
    for m in FREE_MODELS.values():
        text += f"• {m['name']} — {m['desc']}\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("task"))
async def task_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    
    task_text = message.text.replace("/task", "", 1).strip()
    if not task_text:
        await message.answer("❌ <code>/task описание</code>", parse_mode="HTML")
        return
    
    active = await get_active_task(message.chat.id)
    if active:
        await message.answer("⚠️ Есть активная задача. /stop")
        return
    
    model = await get_chat_model(message.chat.id)
    team = await get_chat_team(message.chat.id)
    task = await create_task(message.chat.id, message.from_user.id, task_text, model)
    
    model_name = model.split("/")[-1].replace(":free", "")
    team_str = "".join([AGENT_ROLES.get(a, {}).get("emoji", "") for a in team])
    
    await message.answer(
        f"✅ <b>Задача #{task.id}</b>\n\n"
        f"📝 <i>{task_text}</i>\n\n"
        f"🤖 {model_name}\n"
        f"👥 {team_str}\n"
        "🚀 Начинаю...",
        parse_mode="HTML"
    )
    run_discussion_step.delay(task.id)
    logger.info(f"Task {task.id} model={model} team={team}")

@router.message(Command("status"))
async def status_handler(message: Message):
    task = await get_active_task(message.chat.id)
    if not task:
        await message.answer("📭 Нет задач.")
        return
    await message.answer(f"📊 <b>#{task.id}</b> — {task.current_step}/{task.max_steps}", parse_mode="HTML")

@router.message(Command("stop"))
async def stop_handler(message: Message):
    if not is_allowed(message.from_user.id):
        return
    task = await get_active_task(message.chat.id)
    if not task:
        await message.answer("📭 Нет задач.")
        return
    await update_task_status(task.id, TaskStatus.COMPLETED, "Остановлено.")
    await message.answer(f"🛑 #{task.id} остановлена.")

@router.message(Command("help"))
async def help_handler(message: Message):
    await start_handler(message)

@router.message(F.text)
async def echo_handler(message: Message):
    pass
