from sqlalchemy import select, and_
from app.db.session import get_session
from app.db.models import Task, Message, TaskStatus, ChatSettings
from app.config import settings
from typing import Optional, List

async def create_task(chat_id: int, user_id: int, description: str, model: str = None) -> Task:
    async with get_session() as session:
        task = Task(
            chat_id=chat_id,
            user_id=user_id,
            description=description,
            status=TaskStatus.PENDING,
            model=model or settings.DEFAULT_MODEL,
            max_steps=settings.MAX_STEPS_PER_TASK
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task

async def get_task(task_id: int) -> Optional[Task]:
    async with get_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

async def get_active_task(chat_id: int) -> Optional[Task]:
    async with get_session() as session:
        result = await session.execute(
            select(Task).where(
                and_(
                    Task.chat_id == chat_id,
                    Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
                )
            ).order_by(Task.created_at.desc())
        )
        return result.scalar_one_or_none()

async def update_task(task_id: int, **kwargs):
    async with get_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            for key, value in kwargs.items():
                setattr(task, key, value)
            await session.commit()
            return task
    return None

async def update_task_status(task_id: int, status: TaskStatus, final_answer: str = None):
    return await update_task(task_id, status=status, final_answer=final_answer)

async def get_messages(task_id: int, limit: int = 50) -> List[Message]:
    async with get_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.task_id == task_id)
            .order_by(Message.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

async def add_message(task_id: int, role: str, content: str) -> Message:
    async with get_session() as session:
        message = Message(task_id=task_id, role=role, content=content)
        session.add(message)
        await session.commit()
        await session.refresh(message)
        return message

async def get_chat_model(chat_id: int) -> str:
    async with get_session() as session:
        result = await session.execute(
            select(ChatSettings).where(ChatSettings.chat_id == chat_id)
        )
        cs = result.scalar_one_or_none()
        if cs and cs.model:
            return cs.model
        return settings.DEFAULT_MODEL

async def set_chat_model(chat_id: int, model: str):
    async with get_session() as session:
        result = await session.execute(
            select(ChatSettings).where(ChatSettings.chat_id == chat_id)
        )
        cs = result.scalar_one_or_none()
        if cs:
            cs.model = model
        else:
            cs = ChatSettings(chat_id=chat_id, model=model)
            session.add(cs)
        await session.commit()
