from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, URLInputFile
from aiogram.filters import Command, CommandStart
from app.config import settings, FREE_MODELS
from app.db.crud import create_task, get_active_task, update_task_status, get_chat_model, set_chat_model
from app.db.models import TaskStatus
from app.workers.tasks import run_discussion_step
import logging
import httpx
import urllib.parse

logger = logging.getLogger(__name__)
router = Router()

def is_allowed(user_id: int) -> bool:
    if not settings.allowed_user_ids:
        return True
    return user_id in settings.allowed_user_ids

@router.message(CommandStart())
async def start_handler(message: Message):
    current = await get_chat_model(message.chat.id)
    model_name = current.split("/")[-1].replace(":free", "")
    await message.answer(
        "👋 <b>Привет! Я — координатор команды ИИ-агентов.</b>\n\n"
        "🎯 <b>Команды:</b>\n"
        "• /task <i>описание</i> — поставить задачу\n"
        "• /image <i>описание</i> — сгенерировать картинку\n"
        "• /model — выбрать модель ИИ\n"
        "• /models — список всех моделей\n"
        "• /status — статус задачи\n"
        "• /stop — остановить\n\n"
        f"🤖 Модель: <code>{model_name}</code>",
        parse_mode="HTML"
    )

@router.message(Command("image", "img", "picture", "pic"))
async def image_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    
    # Извлекаем промпт
    prompt = message.text.split(maxsplit=1)
    if len(prompt) < 2:
        await message.answer(
            "🖼️ <b>Генерация изображений</b>\n\n"
            "Использование:\n"
            "<code>/image красивый закат над океаном</code>\n"
            "<code>/img робот в стиле киберпанк</code>\n\n"
            "💡 Добавь детали для лучшего результата!",
            parse_mode="HTML"
        )
        return
    
    prompt_text = prompt[1].strip()
    
    # Отправляем статус
    status_msg = await message.answer("🎨 Генерирую изображение...")
    
    try:
        # Pollinations.ai - бесплатно, без API ключа
        encoded_prompt = urllib.parse.quote(prompt_text)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        
        # Отправляем изображение
        photo = URLInputFile(image_url, filename="generated.png")
        await message.answer_photo(
            photo=photo,
            caption=f"🖼️ <b>{prompt_text}</b>\n\n<i>Сгенерировано с Pollinations.ai</i>",
            parse_mode="HTML"
        )
        
        # Удаляем статус
        await status_msg.delete()
        
        logger.info(f"Image generated: {prompt_text[:50]}")
        
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await status_msg.edit_text(f"❌ Ошибка генерации: {str(e)[:100]}")

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
    await message.answer(
        f"🤖 <b>Выбери модель:</b>\n\nТекущая: <code>{current.split('/')[-1]}</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("model:"))
async def model_callback(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    model_key = callback.data.split(":")[1]
    if model_key not in FREE_MODELS:
        await callback.answer("❌ Не найдена", show_alert=True)
        return
    
    model = FREE_MODELS[model_key]
    await set_chat_model(callback.message.chat.id, model["id"])
    await callback.message.edit_text(
        f"✅ <b>Модель изменена!</b>\n\n"
        f"<b>{model['name']}</b>\n<i>{model['desc']}</i>\n\n"
        f"<code>{model['id']}</code>",
        parse_mode="HTML"
    )
    await callback.answer(f"Выбрана: {model['name']}")

@router.message(Command("models"))
async def models_list_handler(message: Message):
    text = "📋 <b>Бесплатные модели:</b>\n\n"
    
    # Группируем по провайдеру
    openrouter = []
    huggingface = []
    for key, model in FREE_MODELS.items():
        if model.get("provider") == "huggingface":
            huggingface.append(model)
        else:
            openrouter.append(model)
    
    text += "<b>OpenRouter:</b>\n"
    for m in openrouter:
        text += f"• {m['name']} — {m['desc']}\n"
    
    if huggingface:
        text += "\n<b>HuggingFace:</b>\n"
        for m in huggingface:
            text += f"• {m['name']} — {m['desc']}\n"
    
    text += "\nИспользуй /model для выбора"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("task"))
async def task_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    
    task_text = message.text.replace("/task", "", 1).strip()
    if not task_text:
        await message.answer("❌ Укажи задачу: <code>/task описание</code>", parse_mode="HTML")
        return
    
    active = await get_active_task(message.chat.id)
    if active:
        await message.answer("⚠️ Уже есть активная задача. /stop чтобы остановить.")
        return
    
    model = await get_chat_model(message.chat.id)
    task = await create_task(message.chat.id, message.from_user.id, task_text, model)
    model_name = model.split("/")[-1].replace(":free", "")
    
    await message.answer(
        f"✅ <b>Задача #{task.id}</b>\n\n"
        f"📝 <i>{task_text}</i>\n\n"
        f"🤖 Модель: <code>{model_name}</code>\n"
        "🚀 Начинаю...",
        parse_mode="HTML"
    )
    run_discussion_step.delay(task.id)
    logger.info(f"Task {task.id} with model {model}")

@router.message(Command("status"))
async def status_handler(message: Message):
    task = await get_active_task(message.chat.id)
    if not task:
        await message.answer("📭 Нет активных задач.")
        return
    
    emoji = {"PENDING": "⏳", "IN_PROGRESS": "🔄", "COMPLETED": "✅", "FAILED": "❌"}.get(str(task.status.value).upper(), "❓")
    await message.answer(
        f"📊 <b>Задача #{task.id}</b>\n\n"
        f"Статус: {emoji} {task.status.value}\n"
        f"Шаг: {task.current_step}/{task.max_steps}",
        parse_mode="HTML"
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

@router.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "📚 <b>Справка</b>\n\n"
        "<b>Основные команды:</b>\n"
        "• /task <i>текст</i> — поставить задачу команде агентов\n"
        "• /image <i>текст</i> — сгенерировать изображение\n"
        "• /model — выбрать модель ИИ\n"
        "• /status — статус текущей задачи\n"
        "• /stop — остановить задачу\n\n"
        "<b>Агенты:</b>\n"
        "🎯 Координатор — управляет обсуждением\n"
        "🔍 Исследователь — собирает информацию\n"
        "🧐 Критик — проверяет решения\n"
        "⚡ Исполнитель — выполняет задачи",
        parse_mode="HTML"
    )

@router.message(F.text)
async def echo_handler(message: Message):
    pass
