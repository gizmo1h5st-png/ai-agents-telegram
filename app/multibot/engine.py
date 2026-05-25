import asyncio
import hashlib
import logging
import re
import time
import json
import urllib.parse

import httpx
import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, URLInputFile

from app.config import settings, AGENT_BOTS, FREE_MODELS

logger = logging.getLogger(__name__)

ROLE_ORDER = ["coordinator", "researcher", "critic", "executor"]

FALLBACK_MODELS = [
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-4-scout:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "google/gemma-3-27b-it:free",
    "nvidia/llama-3.1-nemotron-nano-8b-v1:free",
    "deepseek-ai/DeepSeek-R1",
]


def detect_next_agent(text):
    t = text.lower()
    if "@researcher1_ai_bot" in t or "исследователь" in t:
        return "researcher"
    if "@criticaibot_bot" in t or "критик" in t:
        return "critic"
    if "@executorai_ai_bot" in t or "исполнитель" in t:
        return "executor"
    if "@coordinator_ai_bot" in t or "координатор" in t:
        return "coordinator"
    return None


def call_llm_sync(system_prompt, messages, task, model):
    models_to_try = [model] + [m for m in FALLBACK_MODELS if m != model]
    for try_model in models_to_try[:5]:
        provider = "openrouter"
        for m in FREE_MODELS.values():
            if m["id"] == try_model:
                provider = m.get("provider", "openrouter")
                break
        if provider == "openrouter" and "/" in try_model and ":free" not in try_model:
            if not try_model.startswith("openai/") and not try_model.startswith("deepseek/"):
                provider = "huggingface"
        full_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"ЗАДАЧА: {task}"},
            *messages
        ]
        if provider == "huggingface":
            if not settings.HUGGINGFACE_API_KEY:
                continue
            url = "https://router.huggingface.co/v1/chat/completions"
            headers = {"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}", "Content-Type": "application/json"}
        else:
            url = f"{settings.OPENROUTER_BASE_URL}/chat/completions"
            headers = {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, headers=headers, json={
                    "model": try_model, "messages": full_messages, "max_tokens": 1024, "temperature": 0.7
                })
                if resp.status_code in (429, 402, 404):
                    logger.warning(f"{resp.status_code} on {try_model}, next...")
                    time.sleep(2)
                    continue
                if resp.status_code != 200:
                    logger.error(f"LLM {resp.status_code} {try_model}: {resp.text[:150]}")
                    continue
                data = resp.json()
                if "choices" not in data or not data["choices"]:
                    continue
                content = data["choices"][0].get("message", {}).get("content")
                if content:
                    return content.strip()
        except Exception as e:
            logger.error(f"LLM err {try_model}: {e}")
            continue
    return None


def search_web(query):
    try:
        from ddgs import DDGS
        r = []
        with DDGS() as d:
            for x in d.text(query, max_results=3):
                r.append(f"- {x.get('title','')}: {x.get('body','')}")
        return "\n".join(r) if r else ""
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
        async def gm_cb(callback: CallbackQuery):
            key = callback.data.split(":")[1]
            if key not in FREE_MODELS:
                await callback.answer("❌")
                return
            m = FREE_MODELS[key]
            await self.redis.setex(f"global_model:{callback.message.chat.id}", 86400, m["id"])
            await callback.message.edit_text(f"✅ {m['name']}\n{m['id']}")
            await callback.answer(m["name"])

        @self.router.callback_query(F.data.startswith("pickagent:"))
        async def pa_cb(callback: CallbackQuery):
            role = callback.data.split(":")[1]
            if role not in AGENT_BOTS:
                await callback.answer("❌")
                return
            btns = []
            row = []
            for k, m in FREE_MODELS.items():
                row.append(InlineKeyboardButton(text=m["name"][:15], callback_data=f"am:{role}:{k}"))
                if len(row) == 2:
                    btns.append(row)
                    row = []
            if row:
                btns.append(row)
            await callback.message.edit_text(f"Модель для {AGENT_BOTS[role]['emoji']} {AGENT_BOTS[role]['name']}:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
            await callback.answer()

        @self.router.callback_query(F.data.startswith("am:"))
        async def am_cb(callback: CallbackQuery):
            parts = callback.data.split(":")
            if len(parts) != 3:
                await callback.answer("❌")
                return
            role, key = parts[1], parts[2]
            if role not in AGENT_BOTS or key not in FREE_MODELS:
                await callback.answer("❌")
                return
            m = FREE_MODELS[key]
            await self.redis.setex(f"agent_model:{callback.message.chat.id}:{role}", 86400, m["id"])
            await callback.message.edit_text(f"✅ {AGENT_BOTS[role]['emoji']} {AGENT_BOTS[role]['name']}: {m['name']}")
            await callback.answer()

        @self.router.callback_query(F.data == "resetmodels")
        async def reset_cb(callback: CallbackQuery):
            cid = callback.message.chat.id
            await self.redis.delete(f"global_model:{cid}")
            for r in ROLE_ORDER:
                await self.redis.delete(f"agent_model:{cid}:{r}")
            await callback.message.edit_text("🔄 Сброшено.")
            await callback.answer()

        @self.router.callback_query(F.data.startswith("setsteps:"))
        async def ss_cb(callback: CallbackQuery):
            v = int(callback.data.split(":")[1])
            await self.redis.setex(f"max_steps:{callback.message.chat.id}", 86400, str(v))
            await callback.message.edit_text(f"✅ Шагов: {v}")
            await callback.answer()

        @self.router.callback_query(F.data.startswith("setdelay:"))
        async def sd_cb(callback: CallbackQuery):
            v = int(callback.data.split(":")[1])
            await self.redis.setex(f"delay:{callback.message.chat.id}", 86400, str(v))
            await callback.message.edit_text(f"✅ Задержка: {v}с")
            await callback.answer()

    async def _get_my_id(self):
        if not self._my_id:
            me = await self.bot.get_me()
            self._my_id = me.id
        return self._my_id

    async def _get_model(self, chat_id):
        am = await self.redis.get(f"agent_model:{chat_id}:{self.role}")
        if am:
            return am.decode()
        gm = await self.redis.get(f"global_model:{chat_id}")
        if gm:
            return gm.decode()
        return settings.get_agent_model(self.role)

    async def _get_delay(self, chat_id):
        v = await self.redis.get(f"delay:{chat_id}")
        return int(v) if v else settings.MIN_REPLY_INTERVAL

    async def _get_max_steps(self, chat_id):
        v = await self.redis.get(f"max_steps:{chat_id}")
        return int(v) if v else settings.MAX_DISCUSSION_STEPS

    async def _process_message(self, message: Message):
        chat_id = message.chat.id
        text = message.text or ""
        my_id = await self._get_my_id()
        if message.from_user and message.from_user.id == my_id:
            return
        mh = hashlib.md5(f"{message.message_id}".encode()).hexdigest()[:12]
        if await self.redis.exists(f"dedup:{chat_id}:{mh}"):
            return
        await self.redis.setex(f"dedup:{chat_id}:{mh}", 60, "1")
        is_human = not (message.from_user and message.from_user.is_bot)
        if is_human and self.role == "coordinator":
            cmd = text.strip().lower()
            if cmd in ("/start", "/help", "/menu"):
                await self._show_menu(chat_id)
                return
            if cmd in ("/showmodels", "/models"):
                await self._show_models_list(chat_id)
                return
            if cmd == "/model":
                await self._show_model_picker(chat_id)
                return
            if cmd == "/agentmodel":
                await self._show_agent_model_picker(chat_id)
                return
            if cmd in ("/showconfig", "/config"):
                await self._show_config(chat_id)
                return
            if cmd == "/resetmodels":
                await self.redis.delete(f"global_model:{chat_id}")
                for r in ROLE_ORDER:
                    await self.redis.delete(f"agent_model:{chat_id}:{r}")
                await self.bot.send_message(chat_id, "🔄 Сброшено.")
                return
            if cmd == "/steps":
                await self._show_steps_picker(chat_id)
                return
            if cmd == "/delay":
                await self._show_delay_picker(chat_id)
                return
            if cmd.startswith("/search"):
                q = text.replace("/search", "", 1).strip()
                if q:
                    await self.bot.send_message(chat_id, f"🔍 Ищу: {q}...")
                    r = await asyncio.to_thread(search_web, q)
                    await self.bot.send_message(chat_id, r if r else "Ничего не найдено.")
                return
            if cmd.startswith("/image"):
                p = text.replace("/image", "", 1).strip()
                if p:
                    ep = urllib.parse.quote(p)
                    url = f"https://image.pollinations.ai/prompt/{ep}?width=1024&height=1024&nologo=true"
                    try:
                        await self.bot.send_photo(chat_id, photo=URLInputFile(url, filename="g.png"), caption=p)
                    except Exception as e:
                        await self.bot.send_message(chat_id, f"❌ {str(e)[:100]}")
                return
        is_task = text.lower().startswith("задача:") or text.lower().startswith("/task")
        if is_human and is_task and self.role == "coordinator":
            await self._start_discussion(message)
            return
        if is_human and text.strip().lower() == "/stop":
            if self.role == "coordinator":
                await self.redis.delete(f"active_task:{chat_id}")
                await self.bot.send_message(chat_id, "🛑 Остановлено.")
            return
        tid_raw = await self.redis.get(f"active_task:{chat_id}")
        if not tid_raw:
            return
        tid = int(tid_raw)
        ct = await self.redis.get(f"turn:{chat_id}:{tid}")
        if ct and ct.decode() != self.role:
            return
        delay = await self._get_delay(chat_id)
        rk = f"rate:{self.role}:{chat_id}"
        last = await self.redis.get(rk)
        if last and (time.time() - float(last)) < delay:
            return
        ms = await self._get_max_steps(chat_id)
        sr = await self.redis.get(f"steps:{chat_id}:{tid}")
        steps = int(sr) if sr else 0
        if steps >= ms:
            if self.role == "coordinator":
                await self.redis.delete(f"active_task:{chat_id}")
                await self.bot.send_message(chat_id, "🎯 Лимит шагов.")
            return
        td_raw = await self.redis.get(f"task_desc:{chat_id}:{tid}")
        td = td_raw.decode() if td_raw else ""
        history = await self._get_history(chat_id, tid)
        sender = message.from_user.first_name if message.from_user else "Bot"
        await self._save_message(chat_id, tid, sender, text)
        history.append({"role": "user", "content": f"{sender}: {text}"})
        await self._think_and_reply(chat_id, tid, td, history, steps)

    async def _show_menu(self, chat_id):
        gm_raw = await self.redis.get(f"global_model:{chat_id}")
        gm = (gm_raw.decode() if gm_raw else settings.DEFAULT_MODEL).split("/")[-1].replace(":free", "")
        ms = await self._get_max_steps(chat_id)
        dl = await self._get_delay(chat_id)
        btns = [
            [InlineKeyboardButton(text="🤖 Модель", callback_data="cmd:model"),
             InlineKeyboardButton(text="🎛 Агенты", callback_data="cmd:agentmodel")],
            [InlineKeyboardButton(text="📋 Список", callback_data="cmd:models"),
             InlineKeyboardButton(text="⚙️ Конфиг", callback_data="cmd:config")],
            [InlineKeyboardButton(text="📊 Шаги", callback_data="cmd:steps"),
             InlineKeyboardButton(text="⏱ Задержка", callback_data="cmd:delay")],
            [InlineKeyboardButton(text="🔄 Сброс", callback_data="resetmodels")],
        ]
        await self.bot.send_message(chat_id,
            f"🎯 <b>AI Agents Team</b>\n\n"
            f"🤖 Модель: <code>{gm}</code>\n📊 Шагов: {ms}\n⏱ Задержка: {dl}с\n\n"
            f"<b>Задачи:</b>\n• <code>Задача: описание</code>\n• /stop\n\n"
            f"<b>Инструменты:</b>\n• /search запрос\n• /image описание",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_model_picker(self, chat_id):
        gm_raw = await self.redis.get(f"global_model:{chat_id}")
        cur = gm_raw.decode() if gm_raw else settings.DEFAULT_MODEL
        btns = []
        row = []
        for k, m in FREE_MODELS.items():
            mk = "✅" if m["id"] == cur else ""
            row.append(InlineKeyboardButton(text=f"{mk}{m['name'][:14]}", callback_data=f"gm:{k}"))
            if len(row) == 2:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(chat_id, "🤖 Модель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_agent_model_picker(self, chat_id):
        gm_raw = await self.redis.get(f"global_model:{chat_id}")
        default = gm_raw.decode() if gm_raw else settings.DEFAULT_MODEL
        btns = []
        for role in ROLE_ORDER:
            am = await self.redis.get(f"agent_model:{chat_id}:{role}")
            cur = am.decode() if am else default
            short = cur.split("/")[-1].replace(":free", "")[:18]
            btns.append([InlineKeyboardButton(text=f"{AGENT_BOTS[role]['emoji']} {AGENT_BOTS[role]['name']}: {short}", callback_data=f"pickagent:{role}")])
        btns.append([InlineKeyboardButton(text="🔄 Сброс", callback_data="resetmodels")])
        await self.bot.send_message(chat_id, "🎛 Модели агентов:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_models_list(self, chat_id):
        t = "📋 <b>Модели:</b>\n\n<b>OpenRouter:</b>\n"
        for k, m in FREE_MODELS.items():
            if m.get("provider") != "huggingface":
                t += f"• <code>{k}</code> — {m['name']}\n"
        t += "\n<b>HuggingFace:</b>\n"
        for k, m in FREE_MODELS.items():
            if m.get("provider") == "huggingface":
                t += f"• <code>{k}</code> — {m['name']}\n"
        await self.bot.send_message(chat_id, t, parse_mode="HTML")

    async def _show_config(self, chat_id):
        gm_raw = await self.redis.get(f"global_model:{chat_id}")
        gm = (gm_raw.decode() if gm_raw else settings.DEFAULT_MODEL).split("/")[-1].replace(":free", "")
        ms = await self._get_max_steps(chat_id)
        dl = await self._get_delay(chat_id)
        t = f"⚙️ <b>Конфиг:</b>\n\n🌐 Модель: <code>{gm}</code>\n📊 Шагов: {ms}\n⏱ Задержка: {dl}с\n\n"
        for role in ROLE_ORDER:
            am = await self.redis.get(f"agent_model:{chat_id}:{role}")
            e = AGENT_BOTS[role]["emoji"]
            n = AGENT_BOTS[role]["name"]
            t += f"{e} {n}: <code>{am.decode().split('/')[-1] if am else '(общая)'}</code>\n"
        await self.bot.send_message(chat_id, t, parse_mode="HTML")

    async def _show_steps_picker(self, chat_id):
        cur = await self._get_max_steps(chat_id)
        btns = []
        row = []
        for v in [10, 20, 30, 50, 75, 100]:
            mk = "✅" if v == cur else ""
            row.append(InlineKeyboardButton(text=f"{mk}{v}", callback_data=f"setsteps:{v}"))
            if len(row) == 3:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(chat_id, f"📊 Шагов ({cur}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_delay_picker(self, chat_id):
        cur = await self._get_delay(chat_id)
        btns = []
        row = []
        for v in [3, 5, 8, 10, 15, 20]:
            mk = "✅" if v == cur else ""
            row.append(InlineKeyboardButton(text=f"{mk}{v}с", callback_data=f"setdelay:{v}"))
            if len(row) == 3:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(chat_id, f"⏱ Задержка ({cur}с):", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _start_discussion(self, message: Message):
        chat_id = message.chat.id
        text = message.text or ""
        task = text
        for p in ["задача:", "Задача:", "/task"]:
            task = task.replace(p, "", 1)
        task = task.strip()
        if not task:
            await self.bot.send_message(chat_id, "🎯 Задача: описание")
            return
        if await self.redis.get(f"active_task:{chat_id}"):
            await self.bot.send_message(chat_id, "🎯 Уже есть. /stop")
            return
        tid = int(time.time()) % 1000000
        await self.redis.setex(f"active_task:{chat_id}", 7200, str(tid))
        await self.redis.setex(f"task_desc:{chat_id}:{tid}", 7200, task)
        await self.redis.set(f"steps:{chat_id}:{tid}", "0")
        await self.redis.expire(f"steps:{chat_id}:{tid}", 7200)
        await self.redis.setex(f"turn:{chat_id}:{tid}", 600, "coordinator")
        model = await self._get_model(chat_id)
        ms = await self._get_max_steps(chat_id)
        dl = await self._get_delay(chat_id)
        await self.bot.send_message(chat_id,
            f"🎯 Задача!\n\n📝 {task}\n🤖 {model.split('/')[-1]}\n📊 {ms} шагов\n⏱ {dl}с\n\nНачинаю...")
        await asyncio.sleep(2)
        await self._think_and_reply(chat_id, tid, task, [], 0)

    async def _think_and_reply(self, chat_id, task_id, task_desc, history, steps):
        step = steps + 1
        prompt = self.config["prompt"]
        ms = await self._get_max_steps(chat_id)
        if self.role == "coordinator" and step < 5:
            prompt += f"\n\nШаг {step}/{ms}. РАНО для финального ответа."
        llm_msgs = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in history[-10:]]
        model = await self._get_model(chat_id)
        response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, task_desc, model)
        if not response:
            return
        search_results = ""
        for q in re.findall(r'\[SEARCH:\s*(.+?)\]', response):
            r = await asyncio.to_thread(search_web, q)
            if r:
                search_results += f"\n🔎 {q}:\n{r}"
        msg = f"{self.config['emoji']} {response}"
        if search_results:
            msg += search_results
        if len(msg) > 4000:
            msg = msg[:4000] + "..."
        try:
            await self.bot.send_message(chat_id, msg)
        except:
            clean = re.sub(r'<[^>]+>', '', msg)
            try:
                await self.bot.send_message(chat_id, clean)
            except:
                pass
        delay = await self._get_delay(chat_id)
        await self.redis.setex(f"rate:{self.role}:{chat_id}", delay * 2, str(time.time()))
        await self.redis.incr(f"steps:{chat_id}:{task_id}")
        await self._save_message(chat_id, task_id, self.config["name"], msg)
        if "[ФИНАЛЬНЫЙ ОТВЕТ]" in response or "[FINAL]" in response:
            await self.redis.delete(f"active_task:{chat_id}")
            await self.bot.send_message(chat_id, "✅ Завершено!")
            return
        na = detect_next_agent(response)
        if not na:
            idx = ROLE_ORDER.index(self.role) if self.role in ROLE_ORDER else 0
            na = ROLE_ORDER[(idx + 1) % len(ROLE_ORDER)]
              await self.redis.setex(f"turn:{chat_id}:{task_id}", 600, na)
        await asyncio.sleep(delay)
        # Пушим задачу в очередь следующего бота
        await self.redis.setex(f"pending:{chat_id}:{na}", 300, f"{task_id}:{task_desc}")

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
        asyncio.create_task(self._poll_pending())
        await self.dp.start_polling(self.bot)

    async def _poll_pending(self):
        """Проверяет очередь — есть ли задача для этого бота"""
        while True:
            await asyncio.sleep(3)
            try:
                keys = []
                async for key in self.redis.scan_iter(f"pending:*:{self.role}"):
                    keys.append(key)
                for key in keys:
                    val = await self.redis.get(key)
                    if not val:
                        continue
                    await self.redis.delete(key)
                    parts = key.decode().split(":")
                    chat_id = int(parts[1])
                    val_str = val.decode()
                    task_id = int(val_str.split(":")[0])
                    task_desc = ":".join(val_str.split(":")[1:])
                    # Проверяем что задача ещё активна
                    active = await self.redis.get(f"active_task:{chat_id}")
                    if not active:
                        continue
                    # Проверяем turn
                    ct = await self.redis.get(f"turn:{chat_id}:{task_id}")
                    if ct and ct.decode() != self.role:
                        continue
                    # Проверяем rate limit
                    delay = await self._get_delay(chat_id)
                    rk = f"rate:{self.role}:{chat_id}"
                    last = await self.redis.get(rk)
                    if last and (time.time() - float(last)) < delay:
                        await asyncio.sleep(delay)
                    # Собираем историю и отвечаем
                    history = await self._get_history(chat_id, task_id)
                    sr = await self.redis.get(f"steps:{chat_id}:{task_id}")
                    steps = int(sr) if sr else 0
                    await self._think_and_reply(chat_id, task_id, task_desc, history, steps)
            except Exception as e:
                logger.error(f"Poll pending error: {e}")
