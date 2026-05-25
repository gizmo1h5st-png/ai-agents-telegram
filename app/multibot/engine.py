import asyncio
import hashlib
import logging
import re
import time
import json

import httpx
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

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

        @self.router.callback_query(F.data.startswith("cmd:"))
        async def cmd_cb(callback: CallbackQuery):
            if self.role != "coordinator":
                await callback.answer()
                return
            chat_id = callback.message.chat.id
            cmd = callback.data.split(":")[1]
            if cmd == "model":
                await self._show_model_picker(chat_id)
            elif cmd == "agentmodel":
                await self._show_agent_model_picker(chat_id)
            elif cmd == "models":
                await self._show_models_list(chat_id)
            elif cmd == "config":
                await self._show_config(chat_id)
            elif cmd == "steps":
                await self._show_steps_picker(chat_id)
            elif cmd == "delay":
                await self._show_delay_picker(chat_id)
            await callback.answer()

        @self.router.callback_query(F.data.startswith("gm:"))
        async def global_model_cb(callback: CallbackQuery):
            await self._set_global_model(callback)

        @self.router.callback_query(F.data.startswith("pickagent:"))
        async def pick_agent_cb(callback: CallbackQuery):
            await self._pick_agent_for_model(callback)

        @self.router.callback_query(F.data.startswith("am:"))
        async def agent_model_cb(callback: CallbackQuery):
            await self._set_agent_model(callback)

        @self.router.callback_query(F.data == "resetmodels")
        async def reset_cb(callback: CallbackQuery):
            chat_id = callback.message.chat.id
            await self.redis.delete(f"global_model:{chat_id}")
            for r in ROLE_ORDER:
                await self.redis.delete(f"agent_model:{chat_id}:{r}")
            await callback.message.edit_text("🔄 Все модели сброшены.")
            await callback.answer("Сброшено")

        @self.router.callback_query(F.data.startswith("setsteps:"))
        async def set_steps_cb(callback: CallbackQuery):
            val = int(callback.data.split(":")[1])
            chat_id = callback.message.chat.id
            await self.redis.setex(f"max_steps:{chat_id}", 86400, str(val))
            await callback.message.edit_text(f"✅ Макс. шагов: {val}")
            await callback.answer()

        @self.router.callback_query(F.data.startswith("setdelay:"))
        async def set_delay_cb(callback: CallbackQuery):
            val = int(callback.data.split(":")[1])
            chat_id = callback.message.chat.id
            await self.redis.setex(f"delay:{chat_id}", 86400, str(val))
            await callback.message.edit_text(f"✅ Задержка: {val} сек")
            await callback.answer()

    async def _get_my_id(self):
        if not self._my_id:
            me = await self.bot.get_me()
            self._my_id = me.id
        return self._my_id

    async def _get_model(self, chat_id):
        agent_model = await self.redis.get(f"agent_model:{chat_id}:{self.role}")
        if agent_model:
            return agent_model.decode()
        global_model = await self.redis.get(f"global_model:{chat_id}")
        if global_model:
            return global_model.decode()
        return settings.get_agent_model(self.role)

    async def _get_delay(self, chat_id):
        val = await self.redis.get(f"delay:{chat_id}")
        return int(val) if val else settings.MIN_REPLY_INTERVAL

    async def _get_max_steps(self, chat_id):
        val = await self.redis.get(f"max_steps:{chat_id}")
        return int(val) if val else settings.MAX_DISCUSSION_STEPS

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

        if is_human and self.role == "coordinator":
            cmd = text.strip().lower()

            if cmd in ("/start", "/help", "/menu"):
                await self._show_menu(chat_id)
                return

            if cmd == "/showmodels" or cmd == "/models":
                await self._show_models_list(chat_id)
                return

            if cmd == "/model":
                await self._show_model_picker(chat_id)
                return

            if cmd == "/agentmodel":
                await self._show_agent_model_picker(chat_id)
                return

            if cmd == "/showconfig" or cmd == "/config":
                await self._show_config(chat_id)
                return

            if cmd == "/resetmodels":
                await self.redis.delete(f"global_model:{chat_id}")
                for r in ROLE_ORDER:
                    await self.redis.delete(f"agent_model:{chat_id}:{r}")
                await self.bot.send_message(chat_id, "🔄 Все модели сброшены.")
                return

            if cmd == "/steps":
                await self._show_steps_picker(chat_id)
                return

            if cmd == "/delay":
                await self._show_delay_picker(chat_id)
                return

            if cmd.startswith("/setmodel"):
                await self._handle_setmodel(chat_id, text)
                return

            if cmd == "/search" or cmd.startswith("/search "):
                await self._handle_search(chat_id, text)
                return

            if cmd == "/image" or cmd.startswith("/image "):
                await self._handle_image(chat_id, text)
                return

        is_task = text.lower().startswith("задача:") or text.lower().startswith("/task")

        if is_human and is_task and self.role == "coordinator":
            await self._start_discussion(message)
            return

        if is_human and text.strip().lower() in ("/stop",):
            if self.role == "coordinator":
                await self.redis.delete(f"active_task:{chat_id}")
                await self.bot.send_message(chat_id, "🛑 Остановлено.")
            return

        task_id_raw = await self.redis.get(f"active_task:{chat_id}")
        if not task_id_raw:
            return
        task_id = int(task_id_raw)

        turn_key = f"turn:{chat_id}:{task_id}"
        current_turn = await self.redis.get(turn_key)
        if current_turn and current_turn.decode() != self.role:
            return

        delay = await self._get_delay(chat_id)
        rate_key = f"rate:{self.role}:{chat_id}"
        last = await self.redis.get(rate_key)
        if last and (time.time() - float(last)) < delay:
            return

        max_steps = await self._get_max_steps(chat_id)
        step_key = f"steps:{chat_id}:{task_id}"
        steps_raw = await self.redis.get(step_key)
        steps = int(steps_raw) if steps_raw else 0
        if steps >= max_steps:
            if self.role == "coordinator":
                await self.redis.delete(f"active_task:{chat_id}")
                await self.bot.send_message(chat_id, "🎯 Лимит шагов. Завершено.")
            return

        task_desc_raw = await self.redis.get(f"task_desc:{chat_id}:{task_id}")
        task_desc = task_desc_raw.decode() if task_desc_raw else ""
        history = await self._get_history(chat_id, task_id)
        sender = message.from_user.first_name if message.from_user else "Bot"
        await self._save_message(chat_id, task_id, sender, text)
        history.append({"role": "user", "content": f"{sender}: {text}"})
        await self._think_and_reply(chat_id, task_id, task_desc, history, steps)

    # === МЕНЮ ===

    async def _show_menu(self, chat_id):
        gm_raw = await self.redis.get(f"global_model:{chat_id}")
        gm = (gm_raw.decode() if gm_raw else settings.DEFAULT_MODEL).split("/")[-1].replace(":free", "")
        max_steps = await self._get_max_steps(chat_id)
        delay = await self._get_delay(chat_id)
        
        btns = [
            [InlineKeyboardButton(text="🤖 Общая модель", callback_data="cmd:model"),
             InlineKeyboardButton(text="🎛 Модели агентов", callback_data="cmd:agentmodel")],
            [InlineKeyboardButton(text="📋 Список моделей", callback_data="cmd:models"),
             InlineKeyboardButton(text="⚙️ Конфиг", callback_data="cmd:config")],
            [InlineKeyboardButton(text="📊 Макс. шагов", callback_data="cmd:steps"),
             InlineKeyboardButton(text="⏱ Задержка", callback_data="cmd:delay")],
            [InlineKeyboardButton(text="🔄 Сбросить модели", callback_data="resetmodels")],
        ]
        
        await self.bot.send_message(chat_id,
            f"🎯 <b>AI Agents Team — Настройки</b>\n\n"
            f"🤖 Модель: <code>{gm}</code>\n"
            f"📊 Шагов: {max_steps}\n"
            f"⏱ Задержка: {delay} сек\n\n"
            f"<b>Команды задач:</b>\n"
            f"• <code>Задача: описание</code> — запуск\n"
            f"• <code>/task описание</code> — запуск\n"
            f"• /stop — остановить\n\n"
            f"<b>Инструменты:</b>\n"
            f"• /search запрос — поиск\n"
            f"• /image описание — картинка",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_model_picker(self, chat_id):
        gm_raw = await self.redis.get(f"global_model:{chat_id}")
        current = gm_raw.decode() if gm_raw else settings.DEFAULT_MODEL
        btns = []
        row = []
        for key, m in FREE_MODELS.items():
            mark = "✅" if m["id"] == current else ""
            short = m["name"][:15]
            row.append(InlineKeyboardButton(text=f"{mark}{short}", callback_data=f"gm:{key}"))
            if len(row) == 2:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(chat_id, "🤖 <b>Общая модель:</b>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _set_global_model(self, callback: CallbackQuery):
        key = callback.data.split(":")[1]
        if key not in FREE_MODELS:
            await callback.answer("❌")
            return
        m = FREE_MODELS[key]
        chat_id = callback.message.chat.id
        await self.redis.setex(f"global_model:{chat_id}", 86400, m["id"])
        await callback.message.edit_text(f"✅ Модель: <b>{m['name']}</b>\n<code>{m['id']}</code>", parse_mode="HTML")
        await callback.answer(m["name"])

    async def _show_agent_model_picker(self, chat_id):
        gm_raw = await self.redis.get(f"global_model:{chat_id}")
        default = gm_raw.decode() if gm_raw else settings.DEFAULT_MODEL
        btns = []
        for role in ROLE_ORDER:
            am = await self.redis.get(f"agent_model:{chat_id}:{role}")
            current = am.decode() if am else default
            short = current.split("/")[-1].replace(":free", "")[:18]
            emoji = AGENT_BOTS[role]["emoji"]
            name = AGENT_BOTS[role]["name"]
            btns.append([InlineKeyboardButton(text=f"{emoji} {name}: {short}", callback_data=f"pickagent:{role}")])
        btns.append([InlineKeyboardButton(text="🔄 Сбросить все", callback_data="resetmodels")])
        await self.bot.send_message(chat_id, "🎛 <b>Модели агентов:</b>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _pick_agent_for_model(self, callback: CallbackQuery):
        role = callback.data.split(":")[1]
        if role not in AGENT_BOTS:
            await callback.answer("❌")
            return
        agent = AGENT_BOTS[role]
        btns = []
        row = []
        for key, m in FREE_MODELS.items():
            short = m["name"][:15]
            row.append(InlineKeyboardButton(text=short, callback_data=f"am:{role}:{key}"))
            if len(row) == 2:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await callback.message.edit_text(f"🎛 Модель для {agent['emoji']} <b>{agent['name']}</b>:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
        await callback.answer()

    async def _set_agent_model(self, callback: CallbackQuery):
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("❌")
            return
        role, key = parts[1], parts[2]
        if role not in AGENT_BOTS or key not in FREE_MODELS:
            await callback.answer("❌")
            return
        m = FREE_MODELS[key]
        agent = AGENT_BOTS[role]
        chat_id = callback.message.chat.id
        await self.redis.setex(f"agent_model:{chat_id}:{role}", 86400, m["id"])
        await callback.message.edit_text(f"✅ {agent['emoji']} {agent['name']}: <b>{m['name']}</b>\n<code>{m['id']}</code>", parse_mode="HTML")
        await callback.answer(f"{agent['name']} → {m['name']}")

    async def _show_models_list(self, chat_id):
        text = "📋 <b>Модели:</b>\n\n<b>OpenRouter:</b>\n"
        for key, m in FREE_MODELS.items():
            if m.get("provider") != "huggingface":
                text += f"• <code>{key}</code> — {m['name']}\n"
        text += "\n<b>HuggingFace:</b>\n"
        for key, m in FREE_MODELS.items():
            if m.get("provider") == "huggingface":
                text += f"• <code>{key}</code> — {m['name']}\n"
        await self.bot.send_message(chat_id, text, parse_mode="HTML")

    async def _show_config(self, chat_id):
        gm_raw = await self.redis.get(f"global_model:{chat_id}")
        gm = (gm_raw.decode() if gm_raw else settings.DEFAULT_MODEL).split("/")[-1].replace(":free", "")
        max_steps = await self._get_max_steps(chat_id)
        delay = await self._get_delay(chat_id)
        text = f"⚙️ <b>Конфигурация:</b>\n\n🌐 Общая модель: <code>{gm}</code>\n📊 Макс. шагов: {max_steps}\n⏱ Задержка: {delay} сек\n\n<b>Агенты:</b>\n"
        for role in ROLE_ORDER:
            am = await self.redis.get(f"agent_model:{chat_id}:{role}")
            emoji = AGENT_BOTS[role]["emoji"]
            name = AGENT_BOTS[role]["name"]
            if am:
                text += f"{emoji} {name}: <code>{am.decode().split('/')[-1]}</code>\n"
            else:
                text += f"{emoji} {name}: (общая)\n"
        await self.bot.send_message(chat_id, text, parse_mode="HTML")

    async def _show_steps_picker(self, chat_id):
        current = await self._get_max_steps(chat_id)
        btns = []
        row = []
        for v in [10, 20, 30, 50, 75, 100]:
            mark = "✅" if v == current else ""
            row.append(InlineKeyboardButton(text=f"{mark}{v}", callback_data=f"setsteps:{v}"))
            if len(row) == 3:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(chat_id, f"📊 <b>Макс. шагов</b> (сейчас: {current}):",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_delay_picker(self, chat_id):
        current = await self._get_delay(chat_id)
        btns = []
        row = []
        for v in [3, 5, 8, 10, 15, 20]:
            mark = "✅" if v == current else ""
            row.append(InlineKeyboardButton(text=f"{mark}{v}с", callback_data=f"setdelay:{v}"))
            if len(row) == 3:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(chat_id, f"⏱ <b>Задержка между репликами</b> (сейчас: {current} сек):",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _handle_setmodel(self, chat_id, text):
        parts = text.strip().split()
        if len(parts) == 2:
            key = parts[1].lower()
            if key in FREE_MODELS:
                m = FREE_MODELS[key]
                await self.redis.setex(f"global_model:{chat_id}", 86400, m["id"])
                await self.bot.send_message(chat_id, f"✅ Модель: {m['name']}\n{m['id']}")
            else:
                await self.redis.setex(f"global_model:{chat_id}", 86400, key)
                await self.bot.send_message(chat_id, f"✅ Модель: {key}")
        elif len(parts) == 3:
            role, key = parts[1].lower(), parts[2].lower()
            if role not in AGENT_BOTS:
                await self.bot.send_message(chat_id, f"❌ Роль: {role}\nДоступные: coordinator, researcher, critic, executor")
                return
            mid = FREE_MODELS[key]["id"] if key in FREE_MODELS else key
            await self.redis.setex(f"agent_model:{chat_id}:{role}", 86400, mid)
            await self.bot.send_message(chat_id, f"✅ {AGENT_BOTS[role]['emoji']} {AGENT_BOTS[role]['name']}: {mid}")
        else:
            await self.bot.send_message(chat_id, "Использование:\n/setmodel ключ\n/setmodel роль ключ\n/showmodels — список")

    async def _handle_search(self, chat_id, text):
        query = text.replace("/search", "", 1).strip()
        if not query:
            await self.bot.send_message(chat_id, "🔍 /search запрос")
            return
        await self.bot.send_message(chat_id, f"🔍 Ищу: {query}...")
        result = await asyncio.to_thread(search_web, query)
        if result:
            await self.bot.send_message(chat_id, f"🔍 Результаты:\n\n{result}")
        else:
            await self.bot.send_message(chat_id, "🔍 Ничего не найдено.")

    async def _handle_image(self, chat_id, text):
        import urllib.parse
        from aiogram.types import URLInputFile
        prompt = text.replace("/image", "", 1).strip()
        if not prompt:
            await self.bot.send_message(chat_id, "🖼 /image описание")
            return
        await self.bot.send_message(chat_id, "🎨 Генерирую...")
        try:
            ep = urllib.parse.quote(prompt)
            url = f"https://image.pollinations.ai/prompt/{ep}?width=1024&height=1024&nologo=true"
            photo = URLInputFile(url, filename="gen.png")
            await self.bot.send_photo(chat_id, photo=photo, caption=f"🖼 {prompt}")
        except Exception as e:
            await self.bot.send_message(chat_id, f"❌ {str(e)[:120]}")

    # === ЗАДАЧА ===

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
            await self.bot.send_message(chat_id, "🎯 Уже есть обсуждение. /stop")
            return

        max_steps = await self._get_max_steps(chat_id)
        task_id = int(time.time()) % 1000000
        await self.redis.setex(f"active_task:{chat_id}", 7200, str(task_id))
        await self.redis.setex(f"task_desc:{chat_id}:{task_id}", 7200, task)
        await self.redis.set(f"steps:{chat_id}:{task_id}", "0")
        await self.redis.expire(f"steps:{chat_id}:{task_id}", 7200)
        await self.redis.setex(f"turn:{chat_id}:{task_id}", 600, "coordinator")

        model = await self._get_model(chat_id)
        model_short = model.split("/")[-1].replace(":free", "")
        delay = await self._get_delay(chat_id)

        await self.bot.send_message(chat_id,
            f"🎯 Задача принята!\n\n📝 {task}\n🤖 Модель: {model_short}\n📊 Шагов: {max_steps}\n⏱ Задержка: {delay}с\n\nНачинаю...")

        await asyncio.sleep(2)
        await self._think_and_reply(chat_id, task_id, task, [], 0)

    async def _think_and_reply(self, chat_id, task_id, task_desc, history, steps):
        step = steps + 1
        prompt = self.config["prompt"]
        max_steps = await self._get_max_steps(chat_id)
        if self.role == "coordinator" and step < 5:
            prompt += f"\n\nШаг {step}/{max_steps}. Ещё РАНО для финального ответа."

        llm_msgs = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in history[-10:]]
        model = await self._get_model(chat_id)
        response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, task_desc, model)

              if not response:
            # Fallback: попробовать другую модель
            fallback_models = ["deepseek/deepseek-chat-v3-0324:free", "meta-llama/llama-4-scout:free", "mistralai/mistral-small-3.1-24b-instruct:free"]
            for fb in fallback_models:
                if fb != model:
                    response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, task_desc, fb)
                    if response:
                        logger.info(f"Fallback to {fb} succeeded")
                        break
            if not response:
                await asyncio.sleep(10)
                # Retry с той же моделью
                response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, task_desc, model)
            if not response:
                logger.error(f"All models failed for task {task_id}")
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

        delay = await self._get_delay(chat_id)
        await self.redis.setex(f"rate:{self.role}:{chat_id}", delay * 2, str(time.time()))
        await self.redis.incr(f"steps:{chat_id}:{task_id}")
        await self._save_message(chat_id, task_id, self.config["name"], msg_text)

        if "[ФИНАЛЬНЫЙ ОТВЕТ]" in response or "[FINAL]" in response:
            await self.redis.delete(f"active_task:{chat_id}")
            await self.bot.send_message(chat_id, "✅ Задача завершена!")
            return

        next_agent = detect_next_agent(response)
        if not next_agent:
            idx = ROLE_ORDER.index(self.role) if self.role in ROLE_ORDER else 0
            next_agent = ROLE_ORDER[(idx + 1) % len(ROLE_ORDER)]
        await self.redis.setex(f"turn:{chat_id}:{task_id}", 600, next_agent)
        await asyncio.sleep(delay)

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
