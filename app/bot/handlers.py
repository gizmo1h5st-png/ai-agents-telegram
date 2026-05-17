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
    get_memories,
    clear_memories,
)
from app.db.models import TaskStatus
from app.workers.tasks import run_discussion_step

import logging
import urllib.parse
import html
import io

logger = logging.getLogger(__name__)
router = Router()


def allowed(uid):
    if not settings.allowed_user_ids:
        return True
    return uid in settings.allowed_user_ids


def safe(t):
    return html.escape(str(t or ""))


# ===================== /start =====================

@router.message(CommandStart())
async def start_handler(message: Message):
    m = await get_chat_model(message.chat.id)
    t = await get_chat_team(message.chat.id)
    mn = m.split("/")[-1].replace(":free", "")
    te = "".join([AGENT_ROLES.get(a, {}).get("emoji", "?") for a in t])

    await message.answer(
        "👋 <b>AI Agents Team V2</b>\n\n"
        "🎯 <b>Команды:</b>\n"
        "• /task <i>описание</i> — задача для команды\n"
        "• /templates — шаблоны задач\n"
        "• /image <i>описание</i> — генерация картинки\n"
        "• /search <i>запрос</i> — поиск в интернете\n"
        "• /voice🎤 Голосовое — распознавание речи\n"
        "• /team — выбрать команду агентов\n"
        "• /roles — все роли агентов\n"
        "• /model — выбрать модель ИИ\n"
        "• /models — список моделей\n"
        "• /memory — память агентов\n"
        "• /forget — очистить память\n"
        "• /status — статус задачи\n"
        "• /stop — остановить\n\n"
        f"🤖 Модель: <code>{safe(mn)}</code>\n"
        f"👥 Команда: {te}",
        parse_mode="HTML",
    )


# ===================== /help =====================

@router.message(Command("help"))
async def help_handler(message: Message):
    await start_handler(message)


# ===================== /roles =====================

@router.message(Command("roles"))
async def roles_handler(message: Message):
    text = "🎭 <b>Роли агентов</b>\n\n"
    for key, agent in AGENT_ROLES.items():
        text += (
            f"{agent['emoji']} <b>{safe(agent['name'])}</b>\n"
            f"— {safe(agent['desc'])}\n"
            f"<code>@{safe(key)}</code>\n\n"
        )
    text += "/team — выбрать команду"
    await message.answer(text, parse_mode="HTML")


# ===================== /memory =====================

@router.message(Command("memory"))
async def memory_handler(message: Message):
    mems = await get_memories(message.chat.id)
    if not mems:
        await message.answer("🧠 Память пуста. Она заполняется по мере работы агентов.")
        return
    text = "🧠 <b>Память агентов</b>\n\n"
    for m in mems:
        text += f"<b>[{safe(m.category)}]</b> {safe(m.value)[:100]}\n"
    await message.answer(text, parse_mode="HTML")


# ===================== /forget =====================

@router.message(Command("forget"))
async def forget_handler(message: Message):
    if not allowed(message.from_user.id):
        return
    await clear_memories(message.chat.id)
    await message.answer("🗑️ Память очищена.")


# ===================== /team =====================

@router.message(Command("team"))
async def team_handler(message: Message):
    if not allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    ct = await get_chat_team(message.chat.id)
    cs = "\n".join(
        [
            f"{AGENT_ROLES.get(a, {}).get('emoji', '?')} {safe(AGENT_ROLES.get(a, {}).get('name', a))}"
            for a in ct
        ]
    )

    btns = []
    for key, tp in TEAM_TEMPLATES.items():
        emojis = "".join([AGENT_ROLES.get(a, {}).get("emoji", "") for a in tp["agents"]])
        btns.append(
            [InlineKeyboardButton(text=f"{tp['name']} {emojis}", callback_data=f"team:{key}")]
        )
    btns.append([InlineKeyboardButton(text="🛠️ Собрать свою", callback_data="team:custom")])

    await message.answer(
        f"👥 <b>Команда агентов</b>\n\n<b>Сейчас:</b>\n{cs}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("team:"))
async def team_callback(callback: CallbackQuery):
    if not allowed(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    if not callback.message:
        await callback.answer()
        return

    tk = callback.data.split(":", 1)[1]

    if tk == "custom":
        ct = await get_chat_team(callback.message.chat.id)
        btns = []
        row = []
        for key, agent in AGENT_ROLES.items():
            mark = "✅ " if key in ct else ""
            row.append(
                InlineKeyboardButton(
                    text=f"{mark}{agent['emoji']} {agent['name']}",
                    callback_data=f"agent:{key}",
                )
            )
            if len(row) == 2:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        btns.append([InlineKeyboardButton(text="💾 Сохранить", callback_data="team:save")])

        cn = "\n".join(
            [f"{AGENT_ROLES[a]['emoji']} {safe(AGENT_ROLES[a]['name'])}" for a in ct if a in AGENT_ROLES]
        )
        await callback.message.edit_text(
            f"🛠️ <b>Собери команду</b> (2-6 агентов)\n\n<b>Сейчас:</b>\n{cn}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if tk == "save":
        ct = await get_chat_team(callback.message.chat.id)
        cn = "\n".join(
            [f"{AGENT_ROLES[a]['emoji']} {safe(AGENT_ROLES[a]['name'])}" for a in ct if a in AGENT_ROLES]
        )
        await callback.message.edit_text(
            f"✅ <b>Команда сохранена!</b>\n\n{cn}",
            parse_mode="HTML",
        )
        await callback.answer("Сохранено")
        return

    if tk not in TEAM_TEMPLATES:
        await callback.answer("❌ Не найдено", show_alert=True)
        return

    tp = TEAM_TEMPLATES[tk]
    await set_chat_team(callback.message.chat.id, tp["agents"])
    ts = "\n".join(
        [f"{AGENT_ROLES[a]['emoji']} {safe(AGENT_ROLES[a]['name'])}" for a in tp["agents"] if a in AGENT_ROLES]
    )
    await callback.message.edit_text(
        f"✅ <b>{safe(tp['name'])}</b>\n\n{ts}\n\n<i>{safe(tp['desc'])}</i>",
        parse_mode="HTML",
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("agent:"))
async def agent_toggle_callback(callback: CallbackQuery):
    if not allowed(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    if not callback.message:
        await callback.answer()
        return

    ak = callback.data.split(":", 1)[1]
    ct = await get_chat_team(callback.message.chat.id)

    if ak in ct:
        if len(ct) <= 2:
            await callback.answer("Минимум 2 агента", show_alert=True)
            return
        ct.remove(ak)
        await callback.answer(f"➖ {AGENT_ROLES[ak]['name']}")
    else:
        if len(ct) >= 6:
            await callback.answer("Максимум 6 агентов", show_alert=True)
            return
        ct.append(ak)
        await callback.answer(f"➕ {AGENT_ROLES[ak]['name']}")

    await set_chat_team(callback.message.chat.id, ct)

    btns = []
    row = []
    for key, agent in AGENT_ROLES.items():
        mark = "✅ " if key in ct else ""
        row.append(
            InlineKeyboardButton(
                text=f"{mark}{agent['emoji']} {agent['name']}",
                callback_data=f"agent:{key}",
            )
        )
        if len(row) == 2:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    btns.append([InlineKeyboardButton(text="💾 Сохранить", callback_data="team:save")])

    cn = "\n".join(
        [f"{AGENT_ROLES[a]['emoji']} {safe(AGENT_ROLES[a]['name'])}" for a in ct if a in AGENT_ROLES]
    )
    await callback.message.edit_text(
        f"🛠️ <b>Собери команду</b> (2-6 агентов)\n\n<b>Сейчас:</b>\n{cn}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML",
    )


# ===================== /templates =====================

@router.message(Command("templates"))
async def templates_handler(message: Message):
    btns = []
    for key, tp in TASK_TEMPLATES.items():
        btns.append(
            [InlineKeyboardButton(text=tp["name"], callback_data=f"template:{key}")]
        )
    await message.answer(
        "📚 <b>Шаблоны задач</b>\n\nВыбери шаблон:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("template:"))
async def template_callback(callback: CallbackQuery):
    if not callback.message:
        await callback.answer()
        return

    tk = callback.data.split(":", 1)[1]
    if tk not in TASK_TEMPLATES:
        await callback.answer("❌ Не найден", show_alert=True)
        return

    tp = TASK_TEMPLATES[tk]
    await callback.message.edit_text(
        f"✅ <b>{safe(tp['name'])}</b>\n"
        f"<i>{safe(tp['desc'])}</i>\n\n"
        f"<b>Готовая задача:</b>\n\n"
        f"<pre>{safe(tp['text'])}</pre>\n\n"
        f"Скопируй и отправь:\n"
        f"<code>/task текст задачи</code>",
        parse_mode="HTML",
    )
    await callback.answer()


# ===================== /search =====================

@router.message(Command("search", "find"))
async def search_handler(message: Message):
    query = message.text.split(maxsplit=1)
    if len(query) < 2:
        await message.answer("🔍 <code>/search запрос</code>", parse_mode="HTML")
        return

    qt = query[1].strip()
    sm = await message.answer(f"🔍 Ищу: <i>{safe(qt)}</i>...", parse_mode="HTML")

    try:
        from ddgs import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(qt, max_results=5):
                title = safe(r.get("title", ""))
                body = safe(r.get("body", ""))
                link = safe(r.get("href", ""))
                results.append(f"<b>{title}</b>\n{body}\n🔗 {link}")

        if results:
            text = f"🔍 <b>Результаты: {safe(qt)}</b>\n\n" + "\n\n".join(results[:5])
            await sm.edit_text(text, parse_mode="HTML")
        else:
            await sm.edit_text(f"🔍 Нет результатов: <i>{safe(qt)}</i>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Search error: {e}")
        await sm.edit_text(f"❌ Ошибка: {safe(str(e)[:200])}", parse_mode="HTML")


# ===================== Voice =====================

@router.message(F.voice)
async def voice_handler(message: Message):
    if not allowed(message.from_user.id):
        return

    if not settings.HUGGINGFACE_API_KEY:
        await message.answer("⚠️ Для голосовых нужен HUGGINGFACE_API_KEY.")
        return

    status_msg = await message.answer("🎤 Распознаю речь...")

    try:
        import httpx as hx

        bot = message.bot
        file = await bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        voice_data = buf.getvalue()

        async with hx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://router.huggingface.co/hf-inference/models/openai/whisper-large-v3",
                headers={"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}"},
                content=voice_data,
            )

            if resp.status_code == 503:
                await status_msg.edit_text("⏳ Модель Whisper загружается. Попробуй через 20 сек.")
                return

            if resp.status_code != 200:
                await status_msg.edit_text(f"❌ Ошибка распознавания: {resp.status_code}")
                return

            data = resp.json()
            text = data.get("text", "").strip()

        if not text:
            await status_msg.edit_text("❌ Не удалось распознать речь.")
            return

        await status_msg.edit_text(
            f"🎤 <b>Распознано:</b>\n\n<i>{safe(text)}</i>\n\n"
            f"👉 Скопируй и отправь:\n"
            f"<code>/task {safe(text)}</code>",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Voice error: {e}")
        await status_msg.edit_text(f"❌ {safe(str(e)[:150])}")


# ===================== /image =====================

@router.message(Command("image", "img"))
async def image_handler(message: Message):
    if not allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    prompt = message.text.split(maxsplit=1)
    if len(prompt) < 2:
        await message.answer(
            "🖼️ <b>Генерация картинок</b>\n\n"
            "<code>/image красивый закат над океаном</code>",
            parse_mode="HTML",
        )
        return

    pt = prompt[1].strip()
    sm = await message.answer("🎨 Генерирую...")

    try:
        ep = urllib.parse.quote(pt)
        url = f"https://image.pollinations.ai/prompt/{ep}?width=1024&height=1024&nologo=true"
        photo = URLInputFile(url, filename="generated.png")
        await message.answer_photo(
            photo=photo,
            caption=f"🖼️ <b>{safe(pt)}</b>",
            parse_mode="HTML",
        )
        await sm.delete()
    except Exception as e:
        logger.error(f"Image error: {e}")
        await sm.edit_text(f"❌ Ошибка: {safe(str(e)[:120])}")


# ===================== /model =====================

@router.message(Command("model"))
async def model_handler(message: Message):
    if not allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    current = await get_chat_model(message.chat.id)
    btns = []
    for key, model in FREE_MODELS.items():
        mark = "✅ " if model["id"] == current else ""
        btns.append(
            [InlineKeyboardButton(text=f"{mark}{model['name']}", callback_data=f"model:{key}")]
        )

    await message.answer(
        "🤖 <b>Выбери модель:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("model:"))
async def model_callback(callback: CallbackQuery):
    if not allowed(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    if not callback.message:
        await callback.answer()
        return

    mk = callback.data.split(":", 1)[1]
    if mk not in FREE_MODELS:
        await callback.answer("❌ Не найдена", show_alert=True)
        return

    model = FREE_MODELS[mk]
    await set_chat_model(callback.message.chat.id, model["id"])
    await callback.message.edit_text(
        f"✅ <b>{safe(model['name'])}</b>\n"
        f"<i>{safe(model['desc'])}</i>\n\n"
        f"<code>{safe(model['id'])}</code>",
        parse_mode="HTML",
    )
    await callback.answer(f"Выбрана: {model['name']}")


# ===================== /models =====================

@router.message(Command("models"))
async def models_list_handler(message: Message):
    text = "📋 <b>Модели</b>\n\n"

    or_models = [m for m in FREE_MODELS.values() if m.get("provider") != "huggingface"]
    hf_models = [m for m in FREE_MODELS.values() if m.get("provider") == "huggingface"]

    if or_models:
        text += "<b>OpenRouter:</b>\n"
        for m in or_models:
            text += f"• {safe(m['name'])} — {safe(m['desc'])}\n"

    if hf_models:
        text += "\n<b>HuggingFace:</b>\n"
        for m in hf_models:
            text += f"• {safe(m['name'])} — {safe(m['desc'])}\n"

    await message.answer(text, parse_mode="HTML")


# ===================== /task =====================

@router.message(Command("task"))
async def task_handler(message: Message):
    if not allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    tt = message.text.replace("/task", "", 1).strip()
    if not tt:
        await message.answer(
            "❌ Укажи задачу:\n<code>/task описание задачи</code>",
            parse_mode="HTML",
        )
        return

    active = await get_active_task(message.chat.id)
    if active:
        await message.answer("⚠️ Есть активная задача. /stop чтобы остановить.")
        return

    model = await get_chat_model(message.chat.id)
    team = await get_chat_team(message.chat.id)
    task = await create_task(message.chat.id, message.from_user.id, tt, model)

    mn = model.split("/")[-1].replace(":free", "")
    te = "".join([AGENT_ROLES.get(a, {}).get("emoji", "") for a in team])

    await message.answer(
        f"✅ <b>Задача #{task.id}</b>\n\n"
        f"📝 <i>{safe(tt)}</i>\n\n"
        f"🤖 Модель: <code>{safe(mn)}</code>\n"
        f"👥 Команда: {te}\n"
        f"📊 Шагов: {task.max_steps}\n"
        "🚀 Начинаю...",
        parse_mode="HTML",
    )

    run_discussion_step.delay(task.id)
    logger.info(f"Task {task.id} model={model} team={team}")


# ===================== /status =====================

@router.message(Command("status"))
async def status_handler(message: Message):
    task = await get_active_task(message.chat.id)
    if not task:
        await message.answer("📭 Нет активных задач.")
        return

    sv = getattr(task.status, "value", str(task.status))
    emoji_map = {
        "pending": "⏳",
        "in_progress": "🔄",
        "completed": "✅",
        "failed": "❌",
        "paused": "⏸",
    }
    emoji = emoji_map.get(str(sv).lower(), "❓")

    await message.answer(
        f"📊 <b>Задача #{task.id}</b>\n\n"
        f"Статус: {emoji} {safe(sv)}\n"
        f"Шаг: {task.current_step}/{task.max_steps}",
        parse_mode="HTML",
    )


# ===================== /stop =====================

@router.message(Command("stop"))
async def stop_handler(message: Message):
    if not allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    task = await get_active_task(message.chat.id)
    if not task:
        await message.answer("📭 Нет задач для остановки.")
        return

    await update_task_status(task.id, TaskStatus.COMPLETED, "Остановлено пользователем.")
    await message.answer(f"🛑 Задача #{task.id} остановлена.")


# ===================== Catch-all =====================

@router.message(F.text)
async def echo_handler(message: Message):
    pass
