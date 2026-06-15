import re
from typing import List, Optional

from app.db.crud import save_memory, get_memories, clear_memories


def _make_key(text: str, max_len: int = 80) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip().lower())
    clean = re.sub(r"[^a-zа-яё0-9 _.-]", "", clean, flags=re.IGNORECASE)
    return clean[:max_len] or "memory"


async def remember(chat_id: int, text: str, category: str = "project", task_id: Optional[int] = None):
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty memory")
    key = _make_key(text)
    await save_memory(chat_id=chat_id, category=category, key=key, value=text, task_id=task_id)
    return {"category": category, "key": key, "value": text}


async def list_chat_memories(chat_id: int, category: Optional[str] = None, limit: int = 20):
    return await get_memories(chat_id=chat_id, category=category, limit=limit)


async def search_chat_memories(chat_id: int, query: str, limit: int = 20):
    query = (query or "").strip().lower()
    if not query:
        return []
    mems = await get_memories(chat_id=chat_id, limit=100)
    scored = []
    terms = [t for t in re.split(r"\s+", query) if t]
    for m in mems:
        hay = f"{m.category} {m.key} {m.value}".lower()
        score = sum(1 for t in terms if t in hay)
        if query in hay:
            score += 5
        if score:
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _score, m in scored[:limit]]


async def clear_chat_memories(chat_id: int):
    await clear_memories(chat_id)


async def build_memory_context(chat_id: int, limit: int = 12) -> str:
    mems = await get_memories(chat_id=chat_id, limit=limit)
    if not mems:
        return ""
    lines = ["\n\nДОЛГОВРЕМЕННАЯ ПАМЯТЬ ПРОЕКТА И ПОЛЬЗОВАТЕЛЯ:"]
    for m in mems:
        lines.append(f"- [{m.category}] {m.value}")
    return "\n".join(lines)


async def save_task_lesson(chat_id: int, task_id: int, final_answer: str | None = None):
    text = (final_answer or "").strip()
    if len(text) > 700:
        text = text[:700] + "..."
    if not text:
        text = "Задача завершена. Подробности см. в истории сообщений."
    lesson = f"Task #{task_id} completed. Итог/урок: {text}"
    await remember(chat_id=chat_id, text=lesson, category="lessons", task_id=task_id)
    return lesson


def format_memories(mems) -> str:
    if not mems:
        return "🧠 Память пуста."
    lines = ["🧠 <b>Память</b>\n"]
    for m in mems:
        value = str(m.value or "")
        if len(value) > 350:
            value = value[:350] + "..."
        lines.append(f"<b>[{m.category}]</b> {value}")
    return "\n\n".join(lines)
