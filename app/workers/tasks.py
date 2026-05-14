from celery import Celery
from app.config import settings
import logging
import httpx

logger = logging.getLogger(__name__)

celery_app = Celery(
    "ai_agents",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_soft_time_limit=120,
    task_time_limit=180,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)


def send_telegram_message(chat_id: int, text: str):
    """Синхронная отправка в Telegram"""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        })
        return resp.json()


def call_openrouter(system_prompt: str, messages: list, task: str) -> str:
    """Синхронный вызов OpenRouter"""
    url = f"{settings.OPENROUTER_BASE_URL}/chat/completions"
    
    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ЗАДАЧА: {task}"},
        *messages
    ]
    
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.DEFAULT_MODEL,
                "messages": full_messages,
                "max_tokens": 1024,
                "temperature": 0.7,
            }
        )
        
        if resp.status_code == 429:
            return "⏸ Превышен лимит API. Подожди минуту."
        
        data = resp.json()
        return data["choices"][0]["message"]["content"]


AGENT_PROMPTS = {
    "coordinator": {
        "emoji": "🎯",
        "name": "Координатор",
        "prompt": """Ты — Координатор команды. Управляй обсуждением.
Назначай агентов: @researcher, @critic, @executor
Если готово: пиши [ФИНАЛЬНЫЙ ОТВЕТ] и текст.
Максимум 3 предложения."""
    },
    "researcher": {
        "emoji": "🔍",
        "name": "Исследователь",
        "prompt": """Ты — Исследователь. Предоставляй информацию.
Кратко излагай (2-4 пункта). Передай слово @critic или @coordinator.
Не выдумывай факты."""
    },
    "critic": {
        "emoji": "🧐", 
        "name": "Критик",
        "prompt": """Ты — Критик. Проверяй решения.
Оценка: ✅ Хорошо / ⚠️ Замечания / ❌ Проблема
Если всё ок — передай @coordinator. Максимум 3 предложения."""
    },
    "executor": {
        "emoji": "⚡",
        "name": "Исполнитель", 
        "prompt": """Ты — Исполнитель. Делай конкретную работу.
Давай готовый результат. После: @critic или @coordinator."""
    },
}


def get_next_agent(messages: list, current_step: int) -> str:
    """Определяет следующего агента"""
    if not messages:
        return "coordinator"
    
    last_content = messages[-1].get("content", "").lower()
    
    if "@researcher" in last_content:
        return "researcher"
    if "@critic" in last_content:
        return "critic"
    if "@executor" in last_content:
        return "executor"
    if "@coordinator" in last_content:
        return "coordinator"
    
    agents = ["coordinator", "researcher", "critic", "executor"]
    return agents[current_step % len(agents)]


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def run_discussion_step(self, task_id: int):
    """Выполняет шаг обсуждения (синхронно)"""
    
    import psycopg2
    from psycopg2.extras import RealDictCursor
    
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        task = cur.fetchone()
        
        if not task:
            conn.close()
            return {"status": "error", "reason": "Task not found"}
        
        if task["status"] not in ("pending", "in_progress"):
            conn.close()
            return {"status": "skipped", "reason": f"Task status: {task['status']}"}
        
        if task["current_step"] >= task["max_steps"]:
            cur.execute(
                "UPDATE tasks SET status = 'completed', final_answer = %s WHERE id = %s",
                ("Достигнут лимит шагов.", task_id)
            )
            conn.commit()
            conn.close()
            send_telegram_message(task["chat_id"], "✅ <b>ЗАДАЧА ВЫПОЛНЕНА</b>\n\nДостигнут лимит шагов.")
            return {"status": "completed"}
        
        cur.execute(
            "SELECT role, content FROM messages WHERE task_id = %s ORDER BY created_at",
            (task_id,)
        )
        messages = [{"role": m["role"], "content": m["content"]} for m in cur.fetchall()]
        
        agent_key = get_next_agent(messages, task["current_step"])
        agent = AGENT_PROMPTS[agent_key]
        
        logger.info(f"Task {task_id}, step {task['current_step'] + 1}, agent: {agent['name']}")
        
        llm_messages = []
        for m in messages[-10:]:
            role = "assistant" if m["role"] != "user" else "user"
            llm_messages.append({"role": role, "content": m["content"]})
        
        response = call_openrouter(agent["prompt"], llm_messages, task["description"])
        
        content = f"{agent['emoji']} <b>{agent['name']}:</b>\n{response}"
        
        cur.execute(
            "INSERT INTO messages (task_id, role, content) VALUES (%s, %s, %s)",
            (task_id, agent_key, content)
        )
        cur.execute(
            "UPDATE tasks SET current_step = %s, status = 'in_progress' WHERE id = %s",
            (task["current_step"] + 1, task_id)
        )
        conn.commit()
        
        send_telegram_message(task["chat_id"], content)
        
        is_final = "[ФИНАЛЬНЫЙ ОТВЕТ]" in response or "[FINAL]" in response
        
        if is_final:
            cur.execute(
                "UPDATE tasks SET status = 'completed', final_answer = %s WHERE id = %s",
                (response, task_id)
            )
            conn.commit()
            conn.close()
            send_telegram_message(task["chat_id"], "✅ <b>ЗАДАЧА ВЫПОЛНЕНА</b>")
            return {"status": "completed"}
        
        conn.close()
        
        run_discussion_step.apply_async(args=[task_id], countdown=3)
        
        return {"status": "continue", "step": task["current_step"] + 1}
        
    except Exception as e:
        logger.error(f"Error in step: {e}")
        raise self.retry(exc=e)
