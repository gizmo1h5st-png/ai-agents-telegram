import asyncio
import hashlib
import logging
import re
import time
import json

import httpx
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message
from aiogram.filters import Command

from app.config import settings, AGENT_BOTS, FREE_MODELS

logger = logging.getLogger(__name__)

ROLE_ORDER = ["coordinator", "researcher", "critic", "executor"]


def detect_next_agent(text):
    text_lower = text.lower()
    if "@researcher1_ai_bot" in text_lower or "исследователь" in text_lower:
        return "researcher"
    if "@criticaibot_bot" in text_lower or "критик" in text_lower:
        return "critic"
    if "@executorai_ai_bot" in text_lower or "исполнитель" in text_lower:
        return "executor"
    if "@coordinator_ai_bot" in text_lower or "координатор" in text_lower:
        return "coordinator"
    return None


def call_llm_sync(system_prompt, messages, task, model):
    provider = "openrouter"
    for m in FREE_MODELS.values():
        if m["id"] == model:
            provider = m.get("provider", "openrouter")
            break

    if provider == "openrouter" and "/" in model and ":free" not in model:
        if not model.startswith("openai/") and not model.startswith("deepseek/"):
            provider = "huggingface"

    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ЗАДАЧА: {task}"},
        *messages
    ]

    if provider == "huggingface":
        if not settings.HUGGINGFACE_API_KEY:
            logger.error("No HUGGINGFACE_API_KEY")
            return None
        url = "https://router.huggingface.co/v1/chat/completions"
        headers = {"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}", "Content-Type": "application/json"}
    else:
        url = f"{settings.OPENROUTER_BASE_URL}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}", "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=headers, json={
                "model": model, "messages": full_messages, "max_tokens": 1024, "temperature": 0.7
            })
            if resp.status_code == 429:
                logger.warning(f"Rate limit: {model}")
                return None
            if resp.status_code != 200:
                logger.error(f"LLM {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            if "choices" not in data or not data["choices"]:
                return None
            content = data["choices"][0].get("message", {}).get("content")
            return content.strip() if content else None
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return None


def search_web(query):
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as d:
            for r in d.text(query, max_results=3):
                results.append(f"- {r.get('title','')}: {r.get('body','')}")
        return "\n".join(results) if results else ""
    except:
        return ""


class AgentBot:
    def __init__(self, role, token, redis_client):
        self.role = role
        self.config = AGENT_BOTS[role]
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.router = Router()
        self.dp.include_router(self.router)
        self.redis = redis_client
        self._my_id = None
        self._setup_handlers()

    def _setup_handlers(self):
        @self.router.message(F.text)
        async def handle_message(message: Message):
            await self._process_message(message)

    async def _get_my_id(self):
        if not self._my_id:
            me = await self.bot.get_me()
            self._my_id = me.id
        return self._my_id

    async def _get_model(self, chat_id):
        """Получает модель: сначала персональную агента, потом общую"""
        agent_model = await self.redis.get(f"agent_model:{chat_id}:{self.role}")
        if agent_model:
            return agent_model.decode()
        global_model = await self.redis.get(f"global_model:{chat_id}")
        if global_model:
            return global_model.decode()
        return settings.get_agent_model(self.role)

    async def _process_message(self, message: Message):
        chat_id = message.chat.id
        text = message.text or ""

        my_id = await self._get_my_id()
        if message.from_user and message.from_user.id == my_id:
            return

        msg_hash = hashlib.md5(f"{message.message_id}".encode()).hexdigest()[:12]
        dedup_key = f"dedup:{chat_id}:{msg_hash}"
        if await self.redis.exists(dedup_key):
            return
        await self.redis.setex(dedup_key, 60, "1")

        is_human = not (message.from_user and message.from_user.is_bot)

        # === КОМАНДЫ УПРАВЛЕНИЯ (только Координатор обрабатывает) ===
        if is_human and self.role == "coordinator":

            # /showmodels — список моделей
            if text.strip().lower() == "/showmodels":
                models_text = "📋 Доступные модели:\n\n"
                for key, m in FREE_MODELS.items():
                    models_text += f"• {key} — {m['name']} ({m['desc']})\n"
                models_text += "\nИспользуй:\n/setmodel ключ — для всех\n/setmodel роль ключ — для одного"
                await self.bot.send_message(chat_id, models_text)
                return

            # /showconfig — текущие настройки
            if text.strip().lower() == "/showconfig":
                config_text = "⚙️ Текущие настройки:\n\n"
                global_model = await self.redis.get(f"global_model:{chat_id}")
                gm = global_model.decode() if global_model else settings.DEFAULT_MODEL
                config_text += f"🌐 Общая модель: {gm.split('/')[-1]}\n\n"
                for role in ROLE_ORDER:
                    agent_model = await self.redis.get(f"agent_model:{chat_id}:{role}")
                    emoji = AGENT_BOTS[role]["emoji"]
                    name = AGENT_BOTS[role]["name"]
                    if agent_model:
                        config_text += f"{emoji} {name}: {agent_model.decode().split('/')[-1]}\n"
                    else:
                        config_text += f"{emoji} {name}: (общая)\n"
                await self.bot.send_message(chat_id, config_text)
                return

            # /setmodel — установка модели
            if text.strip().lower().startswith("/setmodel"):
                parts = text.strip().split()
                if len(parts) == 1:
                    await self.bot.send_message(chat_id,
                        "⚙️ Использование:\n"
                        "/setmodel ключ — общая модель\n"
                        "/setmodel coordinator ключ — для агента\n\n"
                        "/showmodels — список моделей")
                    return

                if len(parts) == 2:
                    # Общая модель
                    model_key = parts[1].lower()
                    if model_key in FREE_MODELS:
                        model_id = FREE_MODELS[model_key]["id"]
                        model_name = FREE_MODELS[model_key]["name"]
                        await self.redis.setex(f"global_model:{chat_id}", 86400, model_id)
                        await self.bot.send_message(chat_id, f"✅ Общая модель: {model_name}\n{model_id}")
                    else:
                        # Попробовать как прямой ID модели
                        await self.redis.setex(f"global_model:{chat_id}", 86400, model_key)
                        await self.bot.send_message(chat_id, f"✅ Общая модель: {model_key}")
                    return

                if len(parts) == 3:
                    # Модель для агента
                    agent_role = parts[1].lower()
                    model_key = parts[2].lower()
                    if agent_role not in AGENT_BOTS:
                        await self.bot.send_message(chat_id, f"❌ Роль не найдена: {agent_role}\nДоступные: coordinator, researcher, critic, executor")
                        return
                    if model_key in FREE_MODELS:
                        model_id = FREE_MODELS[model_key]["id"]
                        model_name = FREE_MODELS[model_key]["name"]
                    else:
                        model_id = model_key
                        model_name = model_key
                    await self.redis.setex(f"agent_model:{chat_id}:{agent_role}", 86400, model_id)
                    emoji = AGENT_BOTS[agent_role]["emoji"]
                    name = AGENT_BOTS[agent_role]["name"]
                    await self.bot.send_message(chat_id, f"✅ {emoji} {name}: {model_name}\n{model_id}")
                    return

            # /resetmodels — сброс
            if text.strip().lower() == "/resetmodels":
                await self.redis.delete(f"global_model:{chat_id}")
                for role in ROLE_ORDER:
                    await self.redis.delete(f"agent_model:{chat_id}:{role}")
                await self.bot.send_message(chat_id, "🔄 Все модели сброшены на default.")
                return

        # === ЗАДАЧА ===
        is_task = text.lower().startswith("задача:") or text.lower().startswith("/task")

        if is_human and is_task and self.role == "coordinator":
            await self._start_discussion(message)
            return

        if is_human and text.strip().lower() in ("/stop",):
            if self.role == "coordinator":
                await self.redis.delete(f"active_task:{chat_id}")
                await self.bot.send_message(chat_id, "🛑 Обсуждение остановлено.")
            return

        # === ОБСУЖДЕНИЕ ===
        task_id_raw = await self.redis.get(f"active_task:{chat_id}")
        if not task_id_raw:
            return

        task_id = int(task_id_raw)

        turn_key = f"turn:{chat_id}:{task_id}"
        current_turn = await self.redis.get(turn_key)
        if current_turn and current_turn.decode() != self.role:
            return

        rate_key = f"rate:{self.role}:{chat_id}"
        last = await self.redis.get(rate_key)
        if last and (time.time() - float(last)) < settings.MIN_REPLY_INTERVAL:
            return

        step_key = f"steps:{chat_id}:{task_id}"
        steps_raw = await self.redis.get(step_key)
        steps = int(steps_raw) if steps_raw else 0
        if steps >= settings.MAX_DISCUSSION_STEPS:
            if self.role == "coordinator":
                await self.redis.delete(f"active_task:{chat_id}")
                await self.bot.send_message(chat_id, "🎯 Лимит шагов. Обсуждение завершено.")
            return

        task_desc_raw = await self.redis.get(f"task_desc:{chat_id}:{task_id}")
        task_desc = task_desc_raw.decode() if task_desc_raw else ""

        history = await self._get_history(chat_id, task_id)

        sender = message.from_user.first_name if message.from_user else "Bot"
        await self._save_message(chat_id, task_id, sender, text)
        history.append({"role": "user", "content": f"{sender}: {text}"})

        await self._think_and_reply(chat_id, task_id, task_desc, history, steps)

    async def _start_discussion(self, message: Message):
        chat_id = message.chat.id
        text = message.text or ""

        task = text
        for prefix in ["задача:", "Задача:", "/task"]:
            task = task.replace(prefix, "", 1)
        task = task.strip()

        if not task:
            await self.bot.send_message(chat_id, "🎯 Укажи задачу: Задача: описание")
            return

        existing = await self.redis.get(f"active_task:{chat_id}")
        if existing:
            await self.bot.send_message(chat_id, "🎯 Уже есть обсуждение. /stop чтобы остановить.")
            return

        task_id = int(time.time()) % 1000000
        await self.redis.setex(f"active_task:{chat_id}", 3600, str(task_id))
        await self.redis.setex(f"task_desc:{chat_id}:{task_id}", 3600, task)
        await self.redis.set(f"steps:{chat_id}:{task_id}", "0")
        await self.redis.expire(f"steps:{chat_id}:{task_id}", 3600)
        await self.redis.setex(f"turn:{chat_id}:{task_id}", 300, "coordinator")

        model = await self._get_model(message.chat.id)
        model_short = model.split("/")[-1].replace(":free", "")

        await self.bot.send_message(chat_id, f"🎯 Задача принята!\n\n📝 {task}\n🤖 Модель: {model_short}\n\nНачинаю координацию...")

        await asyncio.sleep(3)
        await self._think_and_reply(chat_id, task_id, task, [], 0)

    async def _think_and_reply(self, chat_id, task_id, task_desc, history, steps):
        step = steps + 1

        prompt = self.config["prompt"]
        if self.role == "coordinator" and step < 5:
            prompt += f"\n\nСейчас шаг {step}. Ещё РАНО для финального ответа."

        llm_msgs = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in history[-10:]]

        model = await self._get_model(chat_id)
        response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, task_desc, model)

        if not response:
            await asyncio.sleep(30)
            return

        search_results = ""
        for query in re.findall(r'\[SEARCH:\s*(.+?)\]', response):
            result = await asyncio.to_thread(search_web, query)
            if result:
                search_results += f"\n🔎 {query}:\n{result}"

        msg_text = f"{self.config['emoji']} {response}"
        if search_results:
            msg_text += search_results

        if len(msg_text) > 4000:
            msg_text = msg_text[:4000] + "..."

        try:
            await self.bot.send_message(chat_id, msg_text)
        except:
            clean = re.sub(r'<[^>]+>', '', msg_text)
            try:
                await self.bot.send_message(chat_id, clean)
            except:
                pass

        rate_key = f"rate:{self.role}:{chat_id}"
        await self.redis.setex(rate_key, settings.MIN_REPLY_INTERVAL * 2, str(time.time()))

        step_key = f"steps:{chat_id}:{task_id}"
        await self.redis.incr(step_key)

        await self._save_message(chat_id, task_id, self.config["name"], msg_text)

        if "[ФИНАЛЬНЫЙ ОТВЕТ]" in response or "[FINAL]" in response:
            await self.redis.delete(f"active_task:{chat_id}")
            await self.bot.send_message(chat_id, "✅ Задача завершена!")
            return

        next_agent = detect_next_agent(response)
        if not next_agent:
            idx = ROLE_ORDER.index(self.role) if self.role in ROLE_ORDER else 0
            next_agent = ROLE_ORDER[(idx + 1) % len(ROLE_ORDER)]

        turn_key = f"turn:{chat_id}:{task_id}"
        await self.redis.setex(turn_key, 300, next_agent)

        await asyncio.sleep(settings.MIN_REPLY_INTERVAL)

    async def _get_history(self, chat_id, task_id):
        key = f"history:{chat_id}:{task_id}"
        raw = await self.redis.lrange(key, 0, 20)
        return [json.loads(m) for m in raw] if raw else []

    async def _save_message(self, chat_id, task_id, sender, text):
        key = f"history:{chat_id}:{task_id}"
        msg = json.dumps({"role": "user", "content": f"{sender}: {text}"})
        await self.redis.rpush(key, msg)
        await self.redis.ltrim(key, -20, -1)
        await self.redis.expire(key, 7200)

    async def start(self):
        await self.bot.delete_webhook(drop_pending_updates=True)
        me = await self.bot.get_me()
        logger.info(f"🤖 {self.config['emoji']} {self.config['name']} (@{me.username}) started")
        await self.dp.start_polling(self.bot)

    async def stop(self):
        await self.bot.session.close()
