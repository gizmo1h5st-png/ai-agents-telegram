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
FALLBACK_MODELS = ["deepseek/deepseek-chat-v3-0324:free", "meta-llama/llama-4-scout:free", "mistralai/mistral-small-3.1-24b-instruct:free", "google/gemma-3-27b-it:free", "deepseek-ai/DeepSeek-R1"]


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
        full_messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": f"ЗАДАЧА: {task}"}, *messages]
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
                resp = client.post(url, headers=headers, json={"model": try_model, "messages": full_messages, "max_tokens": 1024, "temperature": 0.7})
                if resp.status_code in (429, 402, 404):
                    time.sleep(2)
                    continue
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if "choices" not in data or not data["choices"]:
                    continue
                content = data["choices"][0].get("message", {}).get("content")
                if content:
                    return content.strip()
        except:
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
        async def cmd_cb(cb: CallbackQuery):
            if self.role != "coordinator":
                await cb.answer()
                return
            cid = cb.message.chat.id
            c = cb.data.split(":")[1]
            if c == "model":
                await self._show_model_picker(cid)
            elif c == "agentmodel":
                await self._show_agent_model_picker(cid)
            elif c == "models":
                await self._show_models_list(cid)
            elif c == "config":
                await self._show_config(cid)
            elif c == "steps":
                await self._show_steps_picker(cid)
            elif c == "delay":
                await self._show_delay_picker(cid)
            await cb.answer()

        @self.router.callback_query(F.data.startswith("gm:"))
        async def gm_cb(cb: CallbackQuery):
            k = cb.data.split(":")[1]
            if k not in FREE_MODELS:
                await cb.answer("❌")
                return
            m = FREE_MODELS[k]
            await self.redis.setex(f"global_model:{cb.message.chat.id}", 86400, m["id"])
            await cb.message.edit_text(f"✅ {m['name']}\n{m['id']}")
            await cb.answer(m["name"])

        @self.router.callback_query(F.data.startswith("pickagent:"))
        async def pa_cb(cb: CallbackQuery):
            r = cb.data.split(":")[1]
            if r not in AGENT_BOTS:
                await cb.answer("❌")
                return
            btns = []
            row = []
            for k, m in FREE_MODELS.items():
                row.append(InlineKeyboardButton(text=m["name"][:14], callback_data=f"am:{r}:{k}"))
                if len(row) == 2:
                    btns.append(row)
                    row = []
            if row:
                btns.append(row)
            await cb.message.edit_text(f"{AGENT_BOTS[r]['emoji']} {AGENT_BOTS[r]['name']}:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
            await cb.answer()

        @self.router.callback_query(F.data.startswith("am:"))
        async def am_cb(cb: CallbackQuery):
            p = cb.data.split(":")
            if len(p) != 3 or p[1] not in AGENT_BOTS or p[2] not in FREE_MODELS:
                await cb.answer("❌")
                return
            m = FREE_MODELS[p[2]]
            await self.redis.setex(f"agent_model:{cb.message.chat.id}:{p[1]}", 86400, m["id"])
            await cb.message.edit_text(f"✅ {AGENT_BOTS[p[1]]['emoji']} {AGENT_BOTS[p[1]]['name']}: {m['name']}")
            await cb.answer()

        @self.router.callback_query(F.data == "resetmodels")
        async def rm_cb(cb: CallbackQuery):
            cid = cb.message.chat.id
            await self.redis.delete(f"global_model:{cid}")
            for r in ROLE_ORDER:
                await self.redis.delete(f"agent_model:{cid}:{r}")
            await cb.message.edit_text("🔄 Сброшено.")
            await cb.answer()

        @self.router.callback_query(F.data.startswith("setsteps:"))
        async def ss_cb(cb: CallbackQuery):
            v = int(cb.data.split(":")[1])
            await self.redis.setex(f"max_steps:{cb.message.chat.id}", 86400, str(v))
            await cb.message.edit_text(f"✅ Шагов: {v}")
            await cb.answer()

        @self.router.callback_query(F.data.startswith("setdelay:"))
        async def sd_cb(cb: CallbackQuery):
            v = int(cb.data.split(":")[1])
            await self.redis.setex(f"delay:{cb.message.chat.id}", 86400, str(v))
            await cb.message.edit_text(f"✅ Задержка: {v}с")
            await cb.answer()

    async def _get_my_id(self):
        if not self._my_id:
            self._my_id = (await self.bot.get_me()).id
        return self._my_id

    async def _get_model(self, cid):
        am = await self.redis.get(f"agent_model:{cid}:{self.role}")
        if am:
            return am.decode()
        gm = await self.redis.get(f"global_model:{cid}")
        if gm:
            return gm.decode()
        return settings.get_agent_model(self.role)

    async def _get_delay(self, cid):
        v = await self.redis.get(f"delay:{cid}")
        return int(v) if v else settings.MIN_REPLY_INTERVAL

    async def _get_max_steps(self, cid):
        v = await self.redis.get(f"max_steps:{cid}")
        return int(v) if v else settings.MAX_DISCUSSION_STEPS

    async def _process_message(self, message: Message):
        cid = message.chat.id
        text = message.text or ""
        if message.from_user and message.from_user.id == await self._get_my_id():
            return
        mh = hashlib.md5(f"{message.message_id}".encode()).hexdigest()[:12]
        if await self.redis.exists(f"dd:{cid}:{mh}"):
            return
        await self.redis.setex(f"dd:{cid}:{mh}", 60, "1")
        is_human = not (message.from_user and message.from_user.is_bot)
        if is_human and self.role == "coordinator":
            cmd = text.strip().lower()
            if cmd in ("/start", "/help", "/menu"):
                await self._show_menu(cid)
                return
            if cmd in ("/showmodels", "/models"):
                await self._show_models_list(cid)
                return
            if cmd == "/model":
                await self._show_model_picker(cid)
                return
            if cmd == "/agentmodel":
                await self._show_agent_model_picker(cid)
                return
            if cmd in ("/showconfig", "/config"):
                await self._show_config(cid)
                return
            if cmd == "/resetmodels":
                await self.redis.delete(f"global_model:{cid}")
                for r in ROLE_ORDER:
                    await self.redis.delete(f"agent_model:{cid}:{r}")
                await self.bot.send_message(cid, "🔄 Сброшено.")
                return
            if cmd == "/steps":
                await self._show_steps_picker(cid)
                return
            if cmd == "/delay":
                await self._show_delay_picker(cid)
                return
            if cmd.startswith("/search"):
                q = text.replace("/search", "", 1).strip()
                if q:
                    await self.bot.send_message(cid, f"🔍 Ищу: {q}...")
                    r = await asyncio.to_thread(search_web, q)
                    await self.bot.send_message(cid, r if r else "Ничего.")
                return
            if cmd.startswith("/image"):
                p = text.replace("/image", "", 1).strip()
                if p:
                    try:
                        url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(p)}?width=1024&height=1024&nologo=true"
                        await self.bot.send_photo(cid, photo=URLInputFile(url, filename="g.png"), caption=p)
                    except Exception as e:
                        await self.bot.send_message(cid, f"❌ {str(e)[:100]}")
                return
        is_task = text.lower().startswith("задача:") or text.lower().startswith("/task")
        if is_human and is_task and self.role == "coordinator":
            await self._start_discussion(message)
            return
        if is_human and text.strip().lower() == "/stop":
            if self.role == "coordinator":
                await self.redis.delete(f"active_task:{cid}")
                await self.bot.send_message(cid, "🛑 Остановлено.")
            return

    async def _show_menu(self, cid):
        gmr = await self.redis.get(f"global_model:{cid}")
        gm = (gmr.decode() if gmr else settings.DEFAULT_MODEL).split("/")[-1].replace(":free", "")
        ms = await self._get_max_steps(cid)
        dl = await self._get_delay(cid)
        btns = [
            [InlineKeyboardButton(text="🤖 Модель", callback_data="cmd:model"), InlineKeyboardButton(text="🎛 Агенты", callback_data="cmd:agentmodel")],
            [InlineKeyboardButton(text="📋 Список", callback_data="cmd:models"), InlineKeyboardButton(text="⚙️ Конфиг", callback_data="cmd:config")],
            [InlineKeyboardButton(text="📊 Шаги", callback_data="cmd:steps"), InlineKeyboardButton(text="⏱ Задержка", callback_data="cmd:delay")],
            [InlineKeyboardButton(text="🔄 Сброс", callback_data="resetmodels")],
        ]
        await self.bot.send_message(cid, f"🎯 <b>AI Agents Team</b>\n\n🤖 <code>{gm}</code>\n📊 {ms} шагов\n⏱ {dl}с\n\n<b>Задачи:</b> <code>Задача: описание</code>\n/stop\n/search запрос\n/image описание", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_model_picker(self, cid):
        gmr = await self.redis.get(f"global_model:{cid}")
        cur = gmr.decode() if gmr else settings.DEFAULT_MODEL
        btns, row = [], []
        for k, m in FREE_MODELS.items():
            mk = "✅" if m["id"] == cur else ""
            row.append(InlineKeyboardButton(text=f"{mk}{m['name'][:14]}", callback_data=f"gm:{k}"))
            if len(row) == 2:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(cid, "🤖 Модель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_agent_model_picker(self, cid):
        gmr = await self.redis.get(f"global_model:{cid}")
        df = gmr.decode() if gmr else settings.DEFAULT_MODEL
        btns = []
        for r in ROLE_ORDER:
            am = await self.redis.get(f"agent_model:{cid}:{r}")
            cur = (am.decode() if am else df).split("/")[-1].replace(":free", "")[:18]
            btns.append([InlineKeyboardButton(text=f"{AGENT_BOTS[r]['emoji']} {AGENT_BOTS[r]['name']}: {cur}", callback_data=f"pickagent:{r}")])
        btns.append([InlineKeyboardButton(text="🔄 Сброс", callback_data="resetmodels")])
        await self.bot.send_message(cid, "🎛 Агенты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_models_list(self, cid):
        t = "📋 <b>Модели:</b>\n\n<b>OpenRouter:</b>\n"
        for k, m in FREE_MODELS.items():
            if m.get("provider") != "huggingface":
                t += f"• <code>{k}</code> — {m['name']}\n"
        t += "\n<b>HuggingFace:</b>\n"
        for k, m in FREE_MODELS.items():
            if m.get("provider") == "huggingface":
                t += f"• <code>{k}</code> — {m['name']}\n"
        await self.bot.send_message(cid, t, parse_mode="HTML")

    async def _show_config(self, cid):
        gmr = await self.redis.get(f"global_model:{cid}")
        gm = (gmr.decode() if gmr else settings.DEFAULT_MODEL).split("/")[-1].replace(":free", "")
        ms = await self._get_max_steps(cid)
        dl = await self._get_delay(cid)
        t = f"⚙️ <b>Конфиг:</b>\n\n🌐 <code>{gm}</code>\n📊 {ms}\n⏱ {dl}с\n\n"
        for r in ROLE_ORDER:
            am = await self.redis.get(f"agent_model:{cid}:{r}")
            e = AGENT_BOTS[r]["emoji"]
            n = AGENT_BOTS[r]["name"]
            t += f"{e} {n}: <code>{am.decode().split('/')[-1] if am else '(общая)'}</code>\n"
        await self.bot.send_message(cid, t, parse_mode="HTML")

    async def _show_steps_picker(self, cid):
        cur = await self._get_max_steps(cid)
        btns, row = [], []
        for v in [10, 20, 30, 50, 75, 100]:
            mk = "✅" if v == cur else ""
            row.append(InlineKeyboardButton(text=f"{mk}{v}", callback_data=f"setsteps:{v}"))
            if len(row) == 3:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(cid, f"📊 Шагов ({cur}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_delay_picker(self, cid):
        cur = await self._get_delay(cid)
        btns, row = [], []
        for v in [3, 5, 8, 10, 15, 20]:
            mk = "✅" if v == cur else ""
            row.append(InlineKeyboardButton(text=f"{mk}{v}с", callback_data=f"setdelay:{v}"))
            if len(row) == 3:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        await self.bot.send_message(cid, f"⏱ Задержка ({cur}с):", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _start_discussion(self, message: Message):
        cid = message.chat.id
        text = message.text or ""
        task = text
        for p in ["задача:", "Задача:", "/task"]:
            task = task.replace(p, "", 1)
        task = task.strip()
        if not task:
            await self.bot.send_message(cid, "🎯 Задача: описание")
            return
        if await self.redis.get(f"active_task:{cid}"):
            await self.bot.send_message(cid, "🎯 Уже есть. /stop")
            return
        tid = int(time.time()) % 1000000
        await self.redis.setex(f"active_task:{cid}", 7200, str(tid))
        await self.redis.setex(f"task_desc:{cid}:{tid}", 7200, task)
        await self.redis.set(f"steps:{cid}:{tid}", "0")
        await self.redis.expire(f"steps:{cid}:{tid}", 7200)
        await self.redis.setex(f"turn:{cid}:{tid}", 600, "coordinator")
        model = await self._get_model(cid)
        ms = await self._get_max_steps(cid)
        dl = await self._get_delay(cid)
        await self.bot.send_message(cid, f"🎯 Задача!\n\n📝 {task}\n🤖 {model.split('/')[-1]}\n📊 {ms} шагов ⏱ {dl}с\n\nНачинаю...")
        await asyncio.sleep(2)
        await self._think_and_reply(cid, tid, task, [], 0)

    async def _think_and_reply(self, cid, tid, td, hist, steps):
        step = steps + 1
        prompt = self.config["prompt"]
        ms = await self._get_max_steps(cid)
        if self.role == "coordinator" and step < 5:
            prompt += f"\n\nШаг {step}/{ms}. РАНО для финального ответа."
        llm_msgs = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in hist[-10:]]
        model = await self._get_model(cid)
        response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, td, model)
        if not response:
            return
        sr = ""
        for q in re.findall(r'\[SEARCH:\s*(.+?)\]', response):
            r = await asyncio.to_thread(search_web, q)
            if r:
                sr += f"\n🔎 {q}:\n{r}"
        msg = f"{self.config['emoji']} {response}"
        if sr:
            msg += sr
        if len(msg) > 4000:
            msg = msg[:4000] + "..."
        try:
            await self.bot.send_message(cid, msg)
        except:
            try:
                await self.bot.send_message(cid, re.sub(r'<[^>]+>', '', msg))
            except:
                pass
        delay = await self._get_delay(cid)
        await self.redis.setex(f"rate:{self.role}:{cid}", delay * 2, str(time.time()))
        await self.redis.incr(f"steps:{cid}:{tid}")
        await self._save_message(cid, tid, self.config["name"], msg)
        if "[ФИНАЛЬНЫЙ ОТВЕТ]" in response or "[FINAL]" in response:
            await self.redis.delete(f"active_task:{cid}")
            await self.bot.send_message(cid, "✅ Завершено!")
            return
        na = detect_next_agent(response)
        if not na:
            idx = ROLE_ORDER.index(self.role) if self.role in ROLE_ORDER else 0
            na = ROLE_ORDER[(idx + 1) % len(ROLE_ORDER)]
        await self.redis.setex(f"turn:{cid}:{tid}", 600, na)
        await asyncio.sleep(delay)
        await self.redis.setex(f"pending:{cid}:{na}", 300, f"{tid}:{td}")

    async def _get_history(self, cid, tid):
        raw = await self.redis.lrange(f"history:{cid}:{tid}", 0, 20)
        return [json.loads(m) for m in raw] if raw else []

    async def _save_message(self, cid, tid, sender, text):
        k = f"history:{cid}:{tid}"
        await self.redis.rpush(k, json.dumps({"role": "user", "content": f"{sender}: {text}"}))
        await self.redis.ltrim(k, -20, -1)
        await self.redis.expire(k, 7200)

    async def start(self):
        await self.bot.delete_webhook(drop_pending_updates=True)
        me = await self.bot.get_me()
        logger.info(f"🤖 {self.config['emoji']} {self.config['name']} (@{me.username}) started")
        asyncio.create_task(self._poll_pending())
        await self.dp.start_polling(self.bot)

    async def _poll_pending(self):
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
                    cid = int(parts[1])
                    val_str = val.decode()
                    tid = int(val_str.split(":")[0])
                    td = ":".join(val_str.split(":")[1:])
                    active = await self.redis.get(f"active_task:{cid}")
                    if not active:
                        continue
                    ct = await self.redis.get(f"turn:{cid}:{tid}")
                    if ct and ct.decode() != self.role:
                        continue
                    delay = await self._get_delay(cid)
                    rk = f"rate:{self.role}:{cid}"
                    last = await self.redis.get(rk)
                    if last and (time.time() - float(last)) < delay:
                        await asyncio.sleep(delay)
                    history = await self._get_history(cid, tid)
                    sr = await self.redis.get(f"steps:{cid}:{tid}")
                    steps = int(sr) if sr else 0
                    ms = await self._get_max_steps(cid)
                    if steps >= ms:
                        continue
                    await self._think_and_reply(cid, tid, td, history, steps)
            except Exception as e:
                logger.error(f"Poll error: {e}")

    async def stop(self):
        await self.bot.session.close()
