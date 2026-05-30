import json
import time
from typing import Any, Dict, List

from app.config import AGENT_BOTS


RUN_EVENT_TTL = 60 * 60 * 24 * 7


def _now() -> float:
    return time.time()


async def add_run_event(redis, cid: int, tid: int, event_type: str, role: str | None = None, data: Dict[str, Any] | None = None):
    """Append a structured event to Redis run journal."""
    key = f"run_events:{cid}:{tid}"
    item = {
        "ts": _now(),
        "type": event_type,
        "role": role,
        "data": data or {},
    }
    await redis.rpush(key, json.dumps(item, ensure_ascii=False))
    await redis.ltrim(key, -200, -1)
    await redis.expire(key, RUN_EVENT_TTL)


async def get_run_events(redis, cid: int, tid: int, limit: int = 50) -> List[Dict[str, Any]]:
    key = f"run_events:{cid}:{tid}"
    raw = await redis.lrange(key, -limit, -1)
    events = []
    for x in raw:
        try:
            events.append(json.loads(x))
        except Exception:
            pass
    return events


def create_plan_for_team(task: str, team: list[str]) -> list[dict[str, Any]]:
    """Create a deterministic lightweight plan for the active agent team."""
    plan = []
    order = [r for r in team if r != "coordinator"]
    descriptions = {
        "researcher": "Собрать факты, контекст, ограничения, аналоги и неизвестные.",
        "architect": "Спроектировать структуру решения: компоненты, данные, API, инфраструктура, риски.",
        "executor": "Подготовить практический результат: код, конфиги, план внедрения или готовый артефакт.",
        "qa": "Проверить результат: тест-кейсы, edge cases, критерии приёмки, дефекты.",
        "critic": "Провести критическую проверку: слабые места, противоречия, риски, улучшения.",
    }
    idx = 1
    plan.append({"id": idx, "role": "coordinator", "title": "Постановка и маршрут", "status": "pending"})
    idx += 1
    for role in order:
        plan.append({
            "id": idx,
            "role": role,
            "title": descriptions.get(role, f"Вклад агента {role}"),
            "status": "pending",
        })
        idx += 1
    plan.append({"id": idx, "role": "coordinator", "title": "Финальный ответ и фиксация решения", "status": "pending"})
    return plan


async def save_run_plan(redis, cid: int, tid: int, plan: list[dict[str, Any]]):
    await redis.setex(f"run_plan:{cid}:{tid}", RUN_EVENT_TTL, json.dumps(plan, ensure_ascii=False))


async def get_run_plan(redis, cid: int, tid: int) -> list[dict[str, Any]]:
    raw = await redis.get(f"run_plan:{cid}:{tid}")
    if not raw:
        return []
    try:
        return json.loads(raw.decode())
    except Exception:
        return []


async def mark_plan_role_done(redis, cid: int, tid: int, role: str):
    plan = await get_run_plan(redis, cid, tid)
    changed = False
    for item in plan:
        if item.get("role") == role and item.get("status") != "done":
            item["status"] = "done"
            item["done_at"] = _now()
            changed = True
            break
    if changed:
        await save_run_plan(redis, cid, tid, plan)


def format_plan(plan: list[dict[str, Any]]) -> str:
    if not plan:
        return "План пока не создан."
    lines = []
    for item in plan:
        role = item.get("role", "")
        cfg = AGENT_BOTS.get(role, {})
        icon = "✅" if item.get("status") == "done" else "⏳"
        lines.append(f"{icon} {item.get('id')}. {cfg.get('emoji','')} {cfg.get('name', role)} — {item.get('title','')}")
    return "\n".join(lines)


def format_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return "Событий пока нет."
    lines = []
    for ev in events:
        ts = time.strftime("%H:%M:%S", time.localtime(ev.get("ts", 0)))
        role = ev.get("role") or "system"
        et = ev.get("type")
        data = ev.get("data") or {}
        short = ", ".join([f"{k}={str(v)[:60]}" for k, v in data.items() if v is not None])
        lines.append(f"<code>{ts}</code> · <b>{role}</b> · {et}{(' · ' + short) if short else ''}")
    return "\n".join(lines)

