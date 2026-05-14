from celery import Celery
from app.config import settings, FREE_MODELS
import logging
import httpx

logger = logging.getLogger(__name__)

celery_app = Celery("ai_agents", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
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

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    with httpx.Client(timeout=30) as client:
        client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

def get_provider(model_id):
    """Определяет провайдера по model_id"""
    for key, model in FREE_MODELS.items():
        if model["id"] == model_id:
            return model.get("provider", "openrouter")
    return "openrouter"

def call_openrouter(system_prompt, messages, task_description, model):
    url = f"{settings.OPENROUTER_BASE_URL}/chat/completions"
    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ЗАДАЧА: {task_description}"},
        *messages
    ]
    
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            }, json={
                "model": model,
                "messages": full_messages,
                "max_tokens": 1024,
                "temperature": 0.7,
            })
            
            if resp.status_code == 429:
                return "RATE_LIMIT"
            if resp.status_code != 200:
                logger.error(f"OpenRouter error: {resp.status_code} - {resp.text}")
                return "API_ERROR"
            
            data = resp.json()
            if "choices" not in data or not data["choices"]:
                return "EMPTY_RESPONSE"
            
            content = data["choices"][0].get("message", {}).get("content")
            return content if content else "NO_CONTENT"
    except Exception as e:
        logger.error(f"OpenRouter exception: {e}")
        return "EXCEPTION"

def call_huggingface(system_prompt, messages, task_description, model):
    """Вызов Hugging Face Router API (OpenAI-совместимый)"""
    url = "https://router.huggingface.co/v1/chat/completions"
    
    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ЗАДАЧА: {task_description}"},
        *messages
    ]
    
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers={
                "Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}",
                "Content-Type": "application/json",
            }, json={
                "model": model,
                "messages": full_messages,
                "max_tokens": 1024,
                "temperature": 0.7,
            })
            
            if resp.status_code == 429:
                return "RATE_LIMIT"
            if resp.status_code == 503:
                return "MODEL_LOADING"
            if resp.status_code != 200:
                logger.error(f"HuggingFace error: {resp.status_code} - {resp.text[:300]}")
                return "API_ERROR"
            
            data = resp.json()
            if "choices" not in data or not data["choices"]:
                return "EMPTY_RESPONSE"
            
            content = data["choices"][0].get("message", {}).get("content")
            return content.strip() if content else "NO_CONTENT"
    except Exception as e:
        logger.error(f"HuggingFace exception: {e}")
        return "EXCEPTION"

def call_llm(system_prompt, messages, task_description, model):
    """Универсальный вызов LLM"""
    provider = get_provider(model)
    
    if provider == "huggingface":
        if not settings.HUGGINGFACE_API_KEY:
            return "NO_HF_KEY"
        return call_huggingface(system_prompt, messages, task_description, model)
    else:
        return call_openrouter(system_prompt, messages, task_description, model)

AGENT_PROMPTS = {
    "coordinator": {"emoji": "🎯", "name": "Координатор", "prompt": "Ты — Координатор команды. Управляй обсуждением.\nНазначай: @researcher, @critic, @executor\nЕсли готово: [ФИНАЛЬНЫЙ ОТВЕТ] и текст.\nМаксимум 3 предложения."},
    "researcher": {"emoji": "🔍", "name": "Исследователь", "prompt": "Ты — Исследователь. Предоставляй информацию.\n2-4 пункта. Передай @critic или @coordinator."},
    "critic": {"emoji": "🧐", "name": "Критик", "prompt": "Ты — Критик. Проверяй решения.\nХорошо / Замечания / Проблема\nЕсли ок — @coordinator."},
    "executor": {"emoji": "⚡", "name": "Исполнитель", "prompt": "Ты — Исполнитель. Делай работу.\nДавай результат. После: @critic или @coordinator."},
}

def get_next_agent(messages, current_step):
    if not messages:
        return "coordinator"
    last = messages[-1].get("content", "").lower()
    if "@researcher" in last: return "researcher"
    if "@critic" in last: return "critic"
    if "@executor" in last: return "executor"
    if "@coordinator" in last: return "coordinator"
    return ["coordinator", "researcher", "critic", "executor"][current_step % 4]

@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def run_discussion_step(self, task_id):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    
    conn = None
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        task = cur.fetchone()
        if not task:
            conn.close()
            return {"status": "error"}
        
        status = str(task["status"]).upper().replace("TASKSTATUS.", "")
        if status not in ("PENDING", "IN_PROGRESS"):
            conn.close()
            return {"status": "skipped"}
        
        if task["current_step"] >= task["max_steps"]:
            cur.execute("UPDATE tasks SET status = 'COMPLETED', final_answer = %s WHERE id = %s", ("Лимит шагов.", task_id))
            conn.commit()
            conn.close()
            send_telegram_message(task["chat_id"], "✅ <b>ЗАВЕРШЕНО</b>\n\nЛимит шагов.")
            return {"status": "completed"}
        
        cur.execute("SELECT role, content FROM messages WHERE task_id = %s ORDER BY created_at", (task_id,))
        messages = [{"role": m["role"], "content": m["content"]} for m in cur.fetchall()]
        
        agent_key = get_next_agent(messages, task["current_step"])
        agent = AGENT_PROMPTS[agent_key]
        
        logger.info(f"Task {task_id}, step {task['current_step'] + 1}, agent: {agent['name']}")
        
        llm_messages = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in messages[-10:]]
        
        task_model = task.get("model") or settings.DEFAULT_MODEL
        response = call_llm(agent["prompt"], llm_messages, task["description"], task_model)
        
        if response == "RATE_LIMIT":
            conn.close()
            send_telegram_message(task["chat_id"], "⏸ Rate limit. Повтор через 30 сек...")
            run_discussion_step.apply_async(args=[task_id], countdown=30)
            return {"status": "retry"}
        
        if response == "MODEL_LOADING":
            conn.close()
            send_telegram_message(task["chat_id"], "⏳ Модель загружается. Повтор через 20 сек...")
            run_discussion_step.apply_async(args=[task_id], countdown=20)
            return {"status": "retry"}
        
        if response == "NO_HF_KEY":
            conn.close()
            send_telegram_message(task["chat_id"], "⚠️ Нет API ключа HuggingFace. Выбери другую модель /model")
            return {"status": "error"}
        
        if response in ("API_ERROR", "EMPTY_RESPONSE", "NO_CONTENT", "EXCEPTION"):
            conn.close()
            send_telegram_message(task["chat_id"], f"⚠️ Ошибка: {response}. Попробуй /model")
            return {"status": "error"}
        
        content = f"{agent['emoji']} <b>{agent['name']}:</b>\n{response}"
        
        cur.execute("INSERT INTO messages (task_id, role, content) VALUES (%s, %s, %s)", (task_id, agent_key, content))
        cur.execute("UPDATE tasks SET current_step = %s, status = 'IN_PROGRESS' WHERE id = %s", (task["current_step"] + 1, task_id))
        conn.commit()
        
        send_telegram_message(task["chat_id"], content)
        
        if "[ФИНАЛЬНЫЙ ОТВЕТ]" in response or "[FINAL]" in response:
            cur.execute("UPDATE tasks SET status = 'COMPLETED', final_answer = %s WHERE id = %s", (response, task_id))
            conn.commit()
            conn.close()
            send_telegram_message(task["chat_id"], "✅ <b>ЗАВЕРШЕНО</b>")
            return {"status": "completed"}
        
        conn.close()
        run_discussion_step.apply_async(args=[task_id], countdown=3)
        return {"status": "continue"}
        
    except Exception as e:
        logger.error(f"Error: {e}")
        if conn:
            conn.close()
        raise self.retry(exc=e)
