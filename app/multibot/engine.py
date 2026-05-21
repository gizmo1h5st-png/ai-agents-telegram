import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta

import httpx
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message
from aiogram.filters import Command

from app.config import settings, AGENT_BOTS, FREE_MODELS

logger = logging.getLogger(__name__)


class LoopPrevention:
    """Предотвращение бесконечных петель"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
    
    async def can_reply(self, bot_role: str, chat_id: int, task_id: int) -> bool:
        """Проверяет, может ли бот ответить"""
        # Rate limit: не чаще 1 раза в N секунд
        rate_key = f"rate:{bot_role}:{chat_id}"
        last = await self.redis.get(rate_key)
        if last:
            elapsed = time.time() - float(last)
            if elapsed < settings.MIN_REPLY_INTERVAL:
                logger.info(f"{bot_role} rate limited, {elapsed:.1f}s < {settings.MIN_REPLY_INTERVAL}s")
                return False
        
        # Проверяем чей ход
        turn_key = f"turn:{chat_id}:{task_id}"
        current_turn = await self.redis.get(turn_key)
        if current_turn and current_turn.decode() != bot_role:
            return False
        
        # Проверяем лимит шагов
        step_key = f"steps:{chat_id}:{task_id}"
        steps = await self.redis.get(step_key)
        if steps and int(steps) >= settings.MAX_DISCUSSION_STEPS:
            return False
        
        return True
    
    async def record_reply(self, bot_role: str, chat_id: int, task_id: int):
        """Записывает факт ответа"""
        rate_key = f"rate:{bot_role}:{chat_id}"
        await self.redis.setex(rate_key, settings.MIN_REPLY_INTERVAL * 2, str(time.time()))
        
        step_key = f"steps:{chat_id}:{task_id}"
        await self.redis.incr(step_key)
        await self.redis.expire(step_key, 3600)
    
    async def set_turn(self, chat_id: int, task_id: int, next_role: str):
        """Устанавливает чей ход"""
        turn_key = f"turn:{chat_id}:{task_id}"
        await self.redis.setex(turn_key, 300, next_role)
    
    async def is_duplicate(self, chat_id: int, text: str) -> bool:
        """Проверяет дубликат сообщения"""
        msg_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        dedup_key = f"dedup:{chat_id}:{msg_hash}"
        if await self.redis.exists(dedup_key):
            return True
        await self.redis.setex(dedup_key, 60, "1")
        return False
    
    async def get_step_count(self, chat_id: int, task_id: int) -> int:
        step_key = f"steps:{chat_id}:{task_id}"
        steps = await self.redis.get(step_key)
        return int(steps) if steps else 0


def call_llm_sync(system_prompt, messages, task, model):
    """Синхронный вызов LLM"""
    provider = "openrouter"
    for m in FREE_MODELS.values():
        if m["id"] == model:
            provider = m.get("provider", "openrouter")
            break
    
    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ЗАДАЧА: {task}"},
        *messages
    ]
    
    if provider == "huggingface":
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
                return None
            if resp.status_code != 200:
                return None
            data = resp.json()
            if "choices" not in data or not data["choices"]:
                return None
            content = data["choices"][0].get("message", {}).get("content")
            return content.strip() if content else None
    except:
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


def detect_next_agent(text):
    """Определяет следующего агента из текста"""
    text_lower = text.lower()
    if "@researcher" in text_lower or "исследователь" in text_lower:
        return "researcher"
    if "@critic" in text_lower or "критик" in text_lower:
        return "critic"
    if "@executor" in text_lower or "исполнитель" in text_lower:
        return "executor"
    if "@coordinator" in text_lower or "координатор" in text_lower:
        return "coordinator"
    return None


class AgentBot:
    """Один бот-агент"""
    
    def __init__(self, role: str, token: str, redis_client):
        self.role = role
        self.config = AGENT_BOTS[role]
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.router = Router()
        self.dp.include_router(self.router)
        self.loop_prevention = LoopPrevention(redis_client)
        self.redis = redis_client
        self.model = settings.get_agent_model(role)
        self._setup_handlers()
    
    def _setup_handlers(self):
        @self.router.message(F.text)
        async def handle_message(message: Message):
            await self._process_message(message)
    
    async def _process_message(self, message: Message):
        """Обработка входящего сообщения"""
        chat_id = message.chat.id
        text = message.text or ""
        
        # Игнорируем свои сообщения
        bot_info = await self.bot.get_me()
        if message.from_user and message.from_user.id == bot_info.id:
            return
        
        # Дедупликация
        if await self.loop_prevention.is_duplicate(chat_id, text):
            return
        
        # Получаем task_id из Redis
        task_id_raw = await self.redis.get(f"active_task:{chat_id}")
        
        # Если это команда /task или "Задача:" — Координатор стартует
        is_new_task = text.startswith("/task") or text.lower().startswith("задача:")
        
        if is_new_task and self.role == "coordinator":
            task_desc = text.replace("/task", "").replace("Задача:", "").strip()
            if not task_desc:
                return
            
            # Создаём новую задачу
            task_id = int(time.time()) % 100000
            await self.redis.setex(f"active_task:{chat_id}", 3600, str(task_id))
            await self.redis.setex(f"task_desc:{chat_id}:{task_id}", 3600, task_desc)
            await self.redis.set(f"steps:{chat_id}:{task_id}", "0")
            
            # Координатор начинает
            await self.loop_prevention.set_turn(chat_id, task_id, "coordinator")
            await self._think_and_reply(chat_id, task_id, task_desc, [])
            return
        
        if not task_id_raw:
            return
        
        task_id = int(task_id_raw)
        
        # Проверяем /stop
        if text.startswith("/stop"):
            await self.redis.delete(f"active_task:{chat_id}")
            if self.role == "coordinator":
                await self.bot.send_message(chat_id, "🛑 Задача остановлена.")
            return
        
        # Проверяем чей ход
        if not await self.loop_prevention.can_reply(self.role, chat_id, task_id):
            return
        
        # Проверяем, вызывают ли этого бота
        next_agent = detect_next_agent(text)
        if next_agent and next_agent != self.role:
            # Не мой ход — передаём
            await self.loop_prevention.set_turn(chat_id, task_id, next_agent)
            return
        
        # Собираем историю
        task_desc_raw = await self.redis.get(f"task_desc:{chat_id}:{task_id}")
        task_desc = task_desc_raw.decode() if task_desc_raw else ""
        
        history = await self._get_history(chat_id, task_id)
        
        # Добавляем входящее сообщение в историю
        sender = message.from_user.first_name if message.from_user else "Unknown"
        await self._save_message(chat_id, task_id, sender, text)
        history.append({"role": "user", "content": f"{sender}: {text}"})
        
        # Думаем и отвечаем
        await self._think_and_reply(chat_id, task_id, task_desc, history)
    
    async def _think_and_reply(self, chat_id, task_id, task_desc, history):
        """Генерирует ответ и отправляет в чат"""
        step = await self.loop_prevention.get_step_count(chat_id, task_id)
        
        prompt = self.config["prompt"]
        if self.role == "coordinator" and step < 4:
            prompt += f"\n\n⚠️ Шаг {step+1}. Ещё рано для финального ответа."
        
        # Поиск если нужен
        search_results = ""
        if self.role == "researcher":
            prompt += "\nДля поиска: [SEARCH: запрос]"
        
        # Вызываем LLM
        llm_messages = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in history[-10:]]
        
        response = await asyncio.to_thread(call_llm_sync, prompt, llm_messages, task_desc, self.model)
        
        if not response:
            return
        
        # Обрабатываем поиск
        search_matches = re.findall(r'\[SEARCH:\s*(.+?)\]', response)
        for query in search_matches:
            result = await asyncio.to_thread(search_web, query)
            if result:
                search_results += f"\n🔎 {query}:\n{result}"
        
        # Формируем сообщение
        msg_text = f"{self.config['emoji']} {response}"
        if search_results:
            msg_text += search_results
        
        # Обрезаем
        if len(msg_text) > 4000:
            msg_text = msg_text[:4000] + "..."
        
        # Отправляем
        try:
            await self.bot.send_message(chat_id, msg_text)
        except:
            # Fallback без форматирования
            clean = re.sub(r'<[^>]+>', '', msg_text)
            try:
                await self.bot.send_message(chat_id, clean)
            except:
                pass
        
        # Записываем
        await self.loop_prevention.record_reply(self.role, chat_id, task_id)
        await self._save_message(chat_id, task_id, self.config["name"], msg_text)
        
        # Проверяем завершение
        if "[ФИНАЛЬНЫЙ ОТВЕТ]" in response or "[FINAL]" in response:
            await self.redis.delete(f"active_task:{chat_id}")
            await self.bot.send_message(chat_id, "✅ Задача завершена!")
            return
        
        # Определяем следующего
        next_agent = detect_next_agent(response)
        if next_agent:
            await self.loop_prevention.set_turn(chat_id, task_id, next_agent)
        else:
            # Round-robin
            agents = ["coordinator", "researcher", "critic", "executor"]
            idx = agents.index(self.role) if self.role in agents else 0
            next_role = agents[(idx + 1) % len(agents)]
            await self.loop_prevention.set_turn(chat_id, task_id, next_role)
    
    async def _get_history(self, chat_id, task_id):
        """Получает историю из Redis"""
        key = f"history:{chat_id}:{task_id}"
        raw = await self.redis.lrange(key, 0, 20)
        import json
        return [json.loads(m) for m in raw] if raw else []
    
    async def _save_message(self, chat_id, task_id, sender, text):
        """Сохраняет сообщение в Redis"""
        import json
        key = f"history:{chat_id}:{task_id}"
        msg = json.dumps({"role": "user", "content": f"{sender}: {text}"})
        await self.redis.rpush(key, msg)
        await self.redis.ltrim(key, -20, -1)
        await self.redis.expire(key, 7200)
    
    async def start(self):
        """Запускает бота"""
        await self.bot.delete_webhook(drop_pending_updates=True)
        logger.info(f"🤖 {self.config['emoji']} {self.config['name']} started")
        await self.dp.start_polling(self.bot)
    
    async def stop(self):
        await self.bot.session.close()
