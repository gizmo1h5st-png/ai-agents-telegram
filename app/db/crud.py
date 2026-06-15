from sqlalchemy import select, and_, func
from app.db.session import get_session
from app.db.models import Task, Message, TaskStatus, ChatSettings, AgentMemory, TokenUsageLog
from app.config import settings
from typing import Optional, List
from datetime import datetime

import json as _json

async def create_task(chat_id, user_id, description, model=None):
    async with get_session() as session:
        task = Task(chat_id=chat_id, user_id=user_id, description=description,
                    status=TaskStatus.PENDING, model=model or settings.DEFAULT_MODEL,
                    max_steps=settings.MAX_STEPS_PER_TASK)
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task

async def get_task(task_id):
    async with get_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

async def get_active_task(chat_id):
    async with get_session() as session:
        result = await session.execute(
            select(Task).where(and_(Task.chat_id == chat_id,
                Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
            )).order_by(Task.created_at.desc()))
        return result.scalar_one_or_none()

async def update_task(task_id, **kwargs):
    async with get_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            for k, v in kwargs.items():
                setattr(task, k, v)
            await session.commit()
            return task
    return None

async def update_task_status(task_id, status, final_answer=None):
    return await update_task(task_id, status=status, final_answer=final_answer)

async def get_messages(task_id, limit=50):
    async with get_session() as session:
        result = await session.execute(
            select(Message).where(Message.task_id == task_id)
            .order_by(Message.created_at).limit(limit))
        return list(result.scalars().all())

async def add_message(task_id, role, content, msg_type="broadcast", tokens=0):
    async with get_session() as session:
        msg = Message(task_id=task_id, role=role, content=content, msg_type=msg_type, tokens=tokens)
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        return msg

async def get_chat_model(chat_id):
    async with get_session() as session:
        result = await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        cs = result.scalar_one_or_none()
        return cs.model if cs and cs.model else settings.DEFAULT_MODEL

async def set_chat_model(chat_id, model):
    async with get_session() as session:
        result = await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        cs = result.scalar_one_or_none()
        if cs:
            cs.model = model
        else:
            cs = ChatSettings(chat_id=chat_id, model=model)
            session.add(cs)
        await session.commit()

async def get_chat_team(chat_id):
    async with get_session() as session:
        result = await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        cs = result.scalar_one_or_none()
        return cs.team.split(",") if cs and cs.team else ["coordinator", "researcher", "critic", "executor"]

async def set_chat_team(chat_id, agents):
    async with get_session() as session:
        result = await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        cs = result.scalar_one_or_none()
        team_str = ",".join(agents)
        if cs:
            cs.team = team_str
        else:
            cs = ChatSettings(chat_id=chat_id, team=team_str)
            session.add(cs)
        await session.commit()

async def save_memory(chat_id, category, key, value, task_id=None):
    async with get_session() as session:
        existing = await session.execute(
            select(AgentMemory).where(and_(AgentMemory.chat_id == chat_id, AgentMemory.category == category, AgentMemory.key == key)))
        mem = existing.scalar_one_or_none()
        if mem:
            mem.value = value
            mem.source_task_id = task_id
        else:
            mem = AgentMemory(chat_id=chat_id, category=category, key=key, value=value, source_task_id=task_id)
            session.add(mem)
        await session.commit()

async def get_memories(chat_id, category=None, limit=20):
    async with get_session() as session:
        q = select(AgentMemory).where(AgentMemory.chat_id == chat_id)
        if category:
            q = q.where(AgentMemory.category == category)
        q = q.order_by(AgentMemory.updated_at.desc()).limit(limit)
        result = await session.execute(q)
        return list(result.scalars().all())

async def clear_memories(chat_id):
    async with get_session() as session:
        mems = await session.execute(select(AgentMemory).where(AgentMemory.chat_id == chat_id))
        for m in mems.scalars().all():
            await session.delete(m)
        await session.commit()

async def get_agent_models(chat_id):
    async with get_session() as session:
        result = await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        cs = result.scalar_one_or_none()
        if cs and cs.agent_models:
            try:
                return _json.loads(cs.agent_models)
            except:
                pass
        return {}

async def set_agent_model(chat_id, agent_role, model_id):
    async with get_session() as session:
        result = await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        cs = result.scalar_one_or_none()
        if not cs:
            cs = ChatSettings(chat_id=chat_id)
            session.add(cs)
        
        current = {}
        if cs.agent_models:
            try:
                current = _json.loads(cs.agent_models)
            except:
                pass

        if model_id:
            current[agent_role] = model_id
        elif agent_role in current:
            del current[agent_role]
        
        cs.agent_models = _json.dumps(current)
        await session.commit()

async def clear_agent_models(chat_id):
    async with get_session() as session:
        result = await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        cs = result.scalar_one_or_none()
        if cs:
            cs.agent_models = None
            await session.commit()
