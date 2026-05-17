from celery import Celery
from app.config import settings, FREE_MODELS, AGENT_ROLES
import logging
import re
import hashlib
import json
import httpx

logger = logging.getLogger(__name__)

celery_app = Celery("ai_agents", broker=settings.REDIS_URL, backend=settings.REDIS_URL)
celery_app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    timezone="UTC", task_soft_time_limit=120, task_time_limit=180,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
)

_llm_cache = {}

def send_tg(chat_id, text):
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    with httpx.Client(timeout=30) as c:
        c.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

def search_web(query):
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as d:
            for r in d.text(query, max_results=3):
                results.append(f"- {r.get('title','')}: {r.get('body','')}")
        return "\n".join(results) if results else f"No results: {query}"
    except Exception as e:
        return f"Search error: {e}"

def process_searches(response):
    results = []
    for p in [r'\[SEARCH:\s*(.+?)\]', r'\[ПОИСК:\s*(.+?)\]']:
        for q in re.findall(p, response):
            r = search_web(q.strip())
            results.append(f"\n🔎 <b>Search: {q.strip()}</b>\n{r}")
    return results

def extract_memories(response, chat_id, task_id):
    import psycopg2
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    for p in [r'\[REMEMBER:\s*(.+?)\]', r'\[ЗАПОМНИ:\s*(.+?)\]']:
        for fact in re.findall(p, response):
            try:
                conn = psycopg2.connect(db_url)
                cur = conn.cursor()
                cur.execute("INSERT INTO agent_memory (chat_id, category, key, value, source_task_id) VALUES (%s, %s, %s, %s, %s)",
                    (chat_id, "fact", fact[:200], fact, task_id))
                conn.commit()
                conn.close()
            except:
                pass

def get_memory_context(chat_id):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT value FROM agent_memory WHERE chat_id = %s ORDER BY updated_at DESC LIMIT 10", (chat_id,))
        mems = cur.fetchall()
        conn.close()
        if mems:
            return "\n\nПредыдущие знания:\n" + "\n".join([f"- {m['value']}" for m in mems])
        return ""
    except:
        return ""

def check_daily_limit():
    import psycopg2
    from datetime import datetime
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cur.execute("SELECT COUNT(*) FROM token_usage_log WHERE created_at >= %s AND cached = false", (today,))
        count = cur.fetchone()[0]
        conn.close()
        return count < settings.DAILY_REQUEST_LIMIT
    except:
        return True

def log_usage(task_id, model):
    import psycopg2
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("INSERT INTO token_usage_log (task_id, model, tokens_input, tokens_output) VALUES (%s, %s, %s, %s)",
            (task_id, model, 500, 200))
        conn.commit()
        conn.close()
    except:
        pass

def get_provider(model_id):
    for m in FREE_MODELS.values():
        if m["id"] == model_id:
            return m.get("provider", "openrouter")
    return "openrouter"

def call_llm(system_prompt, messages, task_description, model=None):
    model = model or settings.DEFAULT_MODEL
    ck = hashlib.sha256(json.dumps({"p": system_prompt[:80], "m": str(messages)[-150:], "t": task_description[:80]}, sort_keys=True).encode()).hexdigest()[:16]
    if ck in _llm_cache:
        return _llm_cache[ck]

    provider = get_provider(model)
    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ЗАДАЧА: {task_description}"},
        *messages
    ]

    try:
        if provider == "huggingface":
            if not settings.HUGGINGFACE_API_KEY:
                return "NO_HF_KEY"
            url = "https://router.huggingface.co/v1/chat/completions"
            headers = {"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}", "Content-Type": "application/json"}
        else:
            url = f"{settings.OPENROUTER_BASE_URL}/chat/completions"
            headers = {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}", "Content-Type": "application/json"}

        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=headers, json={"model": model, "messages": full_messages, "max_tokens": 1024, "temperature": 0.7})
            if resp.status_code == 429: return "RATE_LIMIT"
            if resp.status_code == 503: return "MODEL_LOADING"
            if resp.status_code != 200:
                logger.error(f"LLM {resp.status_code}: {resp.text[:200]}")
                return "API_ERROR"
            data = resp.json()
            if "choices" not in data or not data["choices"]: return "EMPTY_RESPONSE"
            content = data["choices"][0].get("message", {}).get("content")
            if not content: return "NO_CONTENT"
            content = content.strip()
            _llm_cache[ck] = content
            if len(_llm_cache) > 100:
                for k in list(_llm_cache.keys())[:50]:
                    _llm_cache.pop(k, None)
            return content
    except Exception as e:
        logger.error(f"LLM: {e}")
        return "EXCEPTION"

def get_next_agent(messages, step, team):
    if not messages:
        return team[0] if team else "coordinator"
    last = messages[-1].get("content", "").lower()
    for ak in AGENT_ROLES:
        if f"@{ak}" in last and ak in team:
            return ak
    return team[step % len(team)]

@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def run_discussion_step(self, task_id):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    db_url = settings.DATABASE_URL
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    conn = None
    try:
        if not check_daily_limit():
            return {"status": "rate_limited_daily"}

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
            send_tg(task["chat_id"], "✅ <b>ЗАВЕРШЕНО</b>\n\nЛимит шагов.")
            return {"status": "completed"}

        cur.execute("SELECT role, content FROM messages WHERE task_id = %s ORDER BY created_at", (task_id,))
        messages = [{"role": m["role"], "content": m["content"]} for m in cur.fetchall()]

        cur.execute("SELECT team FROM chat_settings WHERE chat_id = %s", (task["chat_id"],))
        cs = cur.fetchone()
        team = cs["team"].split(",") if cs and cs.get("team") else ["coordinator", "researcher", "critic", "executor"]

        ak = get_next_agent(messages, task["current_step"], team)
        if ak not in AGENT_ROLES:
            ak = "coordinator"
        agent = AGENT_ROLES[ak]

        logger.info(f"Task {task_id}, step {task['current_step']+1}/{task['max_steps']}, agent: {agent['name']}")

        llm_msgs = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in messages[-12:]]

        team_info = ", ".join([f"@{a}" for a in team])
        memory_ctx = get_memory_context(task["chat_id"])

        full_prompt = f"{agent['prompt']}\nТвоя команда: {team_info}"
        if ak == "researcher":
            full_prompt += "\nДля поиска: [SEARCH: запрос]"
        full_prompt += "\nДля запоминания: [REMEMBER: факт]"
        if memory_ctx:
            full_prompt += memory_ctx

        # Подсказка координатору НЕ завершать рано
        if ak == "coordinator" and task["current_step"] < 6:
            full_prompt += "\n\n⚠️ Сейчас шаг " + str(task["current_step"]+1) + ". Ещё рано для финального ответа. Сначала пусть команда обсудит."

        task_model = task.get("model") or settings.DEFAULT_MODEL
        response = call_llm(full_prompt, llm_msgs, task["description"], task_model)

        log_usage(task_id, task_model)

        if response == "RATE_LIMIT":
            conn.close()
            send_tg(task["chat_id"], "⏸ Rate limit. Повтор через 30 сек...")
            run_discussion_step.apply_async(args=[task_id], countdown=30)
            return {"status": "retry"}

        if response == "MODEL_LOADING":
            conn.close()
            send_tg(task["chat_id"], "⏳ Модель загружается...")
            run_discussion_step.apply_async(args=[task_id], countdown=20)
            return {"status": "retry"}

        if response in ("API_ERROR", "EMPTY_RESPONSE", "NO_CONTENT", "EXCEPTION", "NO_HF_KEY"):
            conn.close()
            send_tg(task["chat_id"], f"⚠️ Ошибка: {response}. /model")
            return {"status": "error"}

        searches = process_searches(response)
        extract_memories(response, task["chat_id"], task_id)

        content = f"{agent['emoji']} <b>{agent['name']}:</b>\n{response}"
        if searches:
            content += "\n" + "\n".join(searches)

        cur.execute("INSERT INTO messages (task_id, role, content, msg_type) VALUES (%s, %s, %s, %s)", (task_id, ak, content, "broadcast"))
        cur.execute("UPDATE tasks SET current_step = %s, status = 'IN_PROGRESS' WHERE id = %s", (task["current_step"]+1, task_id))
        conn.commit()

        send_tg(task["chat_id"], content)

        if "[ФИНАЛЬНЫЙ ОТВЕТ]" in response or "[FINAL ANSWER]" in response or "[FINAL]" in response:
            cur.execute("UPDATE tasks SET status = 'COMPLETED', final_answer = %s WHERE id = %s", (response, task_id))
            conn.commit()
            conn.close()
            send_tg(task["chat_id"], "✅ <b>ЗАВЕРШЕНО</b>")
            return {"status": "completed"}

        conn.close()
        run_discussion_step.apply_async(args=[task_id], countdown=3)
        return {"status": "continue"}

    except Exception as e:
        logger.error(f"Error: {e}")
        if conn:
            conn.close()
        raise self.retry(exc=e)
