from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart
from app.config import settings
from app.db.crud import create_task, get_active_task, update_task_status
from app.db.models import TaskStatus
from app.workers.tasks import run_discussion_step
import logging

logger = logging.getLogger(__name__)
router = Router()

def is_allowed(user_id: int) -> bool:
    if not settings.allowed_user_ids:
        return True
    return user_id in settings.allowed_user_ids

@router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 <b>Привет! Я — координатор команды ИИ-агентов.</b>\n\n"
        "🎯 <b>Поставь задачу командой:</b>\n"
        "<code>/task описание задачи</code>\n\n"
        "Моя команда начнёт обсуждение и выдаст результат прямо в чат.\n\n"
        "<b>📋 Команды:</b>\n"
        "• /task — поставить задачу\n"
        "• /status — статус задачи\n"
        "• /stop — остановить обсуждение",
        parse_mode="HTML"
    )

@router.message(Command("task"))
async def task_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к боту.")
        return
    
    task_text = message.text.replace("/task", "", 1).strip()
    
    if not task_text:
        await message.answer(
            "❌ <b>Укажите задачу после команды.</b>\n\n"
            "Пример: <code>/task Напиши бизнес-план для стартапа</code>",
            parse_mode="HTML"
        )
        return
    
    active = await get_active_task(message.chat.id)
    if active:
        await message.answer(
            "⚠️ В этом чате уже есть активная задача.\n"
            "Используй /stop чтобы завершить её.",
            parse_mode="HTML"
        )
        return
    
    task = await create_task(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        description=task_text
    )
    
    await message.answer(
        f"✅ <b>Задача #{task.id} принята!</b>\n\n"
        f"📝 <i>{task_text}</i>\n\n"
        "🤖 Команда начинает обсуждение...",
        parse_mode="HTML"
    )
    
    # Запускаем обсуждение
    run_discussion_step.delay(task.id)
    logger.info(f"Task {task.id} created, discussion started")

@router.message(Command("status"))
async def status_handler(message: Message):
    task = await get_active_task(message.chat.id)
    
    if not task:
        await message.answer("📭 Нет активных задач в этом чате.")
        return
    
    status_emoji = {
        TaskStatus.PENDING: "⏳",
        TaskStatus.IN_PROGRESS: "🔄",
        TaskStatus.PAUSED: "⏸",
        TaskStatus.COMPLETED: "✅",
        TaskStatus.FAILED: "❌"
    }
    
    emoji = status_emoji.get(task.status, "❓")
    await message.answer(
        f"📊 <b>Задача #{task.id}</b>\n\n"
        f"Статус: {emoji} {task.status.value}\n"
        f"Шаг: {task.current_step}/{task.max_steps}\n"
        f"Создана: {task.created_at.strftime('%H:%M %d.%m')}",
        parse_mode="HTML"
    )

@router.message(Command("stop"))
async def stop_handler(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа.")
        return
    
    task = await get_active_task(message.chat.id)
    
    if not task:
        await message.answer("📭 Нет активных задач для остановки.")
        return
    
    await update_task_status(task.id, TaskStatus.COMPLETED, "Задача остановлена пользователем.")
    await message.answer(f"🛑 Задача #{task.id} остановлена.")

@router.message(F.text)
async def echo_handler(message: Message):
    # Игнорируем обычные сообщения, отвечаем только на команды
    pass
