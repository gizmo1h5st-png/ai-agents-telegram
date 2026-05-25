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
    # Сначала Mistral напрямую — у пользователя есть MISTRAL_API_KEY.
    "mistral-small-latest",
    "open-mistral-nemo",
    "ministral-8b-latest",
    # Затем бесплатные OpenRouter/HuggingFace fallback.
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-4-scout:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "google/gemma-3-27b-it:free",
    "deepseek-ai/DeepSeek-R1",
]


AGENT_MENTIONS = {
    "coordinator": ("@coordintor_ai_bot", "@coordinator_ai_bot"),  # второй — legacy-алиас для старых сообщений
    "researcher": ("@researcher1_ai_bot",),
    "critic": ("@criticaibot_bot",),
    "executor": ("@executorai_ai_bot",),
}

AGENT_NAME_PATTERNS = {
    "coordinator": (r"\bкоординатор(?:у|а|ом|е)?\b",),
    "researcher": (r"\bисследователь(?:ю|я|ем|е)?\b",),
    "critic": (r"\bкритик(?:у|а|ом|е)?\b",),
    "executor": (r"\bисполнитель(?:ю|я|ем|е)?\b",),
}

TURN_MARKERS = (
    "передаю", "передать", "передай", "слово", "следующий", "следующая",
    "пусть", "обратимся", "назначаю", "вызываю", "далее"
)

CORRECT_USERNAMES_PROMPT = """

ВАЖНО: правильные usernames агентов в Telegram:
- Координатор: @coordintor_ai_bot
- Исследователь: @Researcher1_ai_bot
- Критик: @criticaibot_bot
- Исполнитель: @executorai_ai_bot

Не придумывай диалог за других агентов. Не задавай вопросы самому себе.
В конце ответа, если обсуждение не завершено, передай ход ровно одному ДРУГОМУ агенту через его @username.
Никогда не передавай ход самому себе.
Если в истории есть "Замечание пользователя" для тебя — это приоритетная обратная связь: учти её, пересмотри вывод и при необходимости измени точку зрения.
"""

FINALIZATION_PROMPT = """

РЕЖИМ ФИНАЛИЗАЦИИ:
Лимит обсуждения почти достигнут или возникла ошибка модели.
Сейчас нужно завершить обсуждение, а не передавать ход дальше.
Обязательно начни ответ с маркера: [ФИНАЛЬНЫЙ ОТВЕТ]
Дай краткий итог: решение, основные аргументы, ограничения и следующие практические шаги.
Не упоминай следующего агента и не ставь @username в конце.
"""


def normalize_agent_mentions(text):
    """Исправляет старые/ошибочные упоминания перед отправкой и парсингом."""
    if not text:
        return text
    return text.replace("@coordinator_ai_bot", "@coordintor_ai_bot")


def detect_addressed_agent(text):
    """Определяет, какому агенту пользователь адресовал замечание.

    Поддерживает форматы:
    - @Researcher1_ai_bot ты ошибся ...
    - Исследователь, учти ...
    - критик: проверь ещё раз ...
    """
    t = (text or "").lower().strip()
    if not t:
        return None

    # Явные @username.
    for role in ROLE_ORDER:
        if any(m.lower() in t for m in AGENT_MENTIONS.get(role, ())):
            return role

    # Обращение по роли в начале сообщения.
    for role, patterns in AGENT_NAME_PATTERNS.items():
        for pattern in patterns:
            if re.search(rf"^\s*(?:ai\s+)?{pattern}[\s,.:;!—-]+", t):
                return role

    return None


def clean_feedback_text(text):
    """Убирает из замечания явный @username в начале/тексте, чтобы LLM видел суть."""
    cleaned = normalize_agent_mentions(text or "").strip()
    for mentions in AGENT_MENTIONS.values():
        for mention in mentions:
            cleaned = re.sub(re.escape(mention), "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^\s*(координатор|исследователь|критик|исполнитель)[\s,.:;!—-]+", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or (text or "").strip()


FINAL_PATTERNS = (
    r"\[\s*финальн(?:ый|ая|ое)\s+ответ\s*\]",
    r"\[\s*final\s*\]",
    r"^\s*финальн(?:ый|ая|ое)\s+ответ\s*[:：]",
    r"^\s*итогов(?:ый|ая|ое)\s+ответ\s*[:：]",
    r"^\s*итог\s*[:：]",
    r"^\s*окончательн(?:ый|ая|ое)\s+ответ\s*[:：]",
    r"обсуждение\s+завершено",
    r"задача\s+завершена",
)


def is_final_response(text):
    """Распознаёт финальный ответ в разных форматах, а не только строго [ФИНАЛЬНЫЙ ОТВЕТ]."""
    t = (text or "").strip().lower()
    return any(re.search(pattern, t, flags=re.IGNORECASE | re.MULTILINE) for pattern in FINAL_PATTERNS)


def detect_next_agent(text, current_role=None):
    """Определяет следующего агента по ЯВНОЙ передаче хода.

    Важно: не выбираем агента только потому, что он назвал свою роль
    (например: "Как исследователь, ..."). Это и вызывало самодиалог
    Researcher -> Researcher.
    """
    t = (text or "").lower()

    # 1) Явные @mentions имеют приоритет.
    # Если агент упомянул самого себя, игнорируем это и ищем другого адресата.
    for role in ROLE_ORDER:
        if role == current_role:
            continue
        if any(m in t for m in AGENT_MENTIONS.get(role, ())):
            return role

    # 2) Названия ролей считаем адресатом только рядом с маркерами передачи хода.
    if any(marker in t for marker in TURN_MARKERS):
        for role in ROLE_ORDER:
            if role == current_role:
                continue
            for pattern in AGENT_NAME_PATTERNS.get(role, ()):
                if re.search(pattern, t):
                    return role

    return None


def get_provider_for_model(model_id):
    for m in FREE_MODELS.values():
        if m["id"] == model_id:
            return m.get("provider", "openrouter")

    # Прямые модели Mistral API.
    if model_id in ("mistral-small-latest", "open-mistral-nemo", "ministral-8b-latest"):
        return "mistral"

    # HF модели обычно без :free и с org/model.
    if "/" in model_id and ":free" not in model_id:
        if not model_id.startswith(("openai/", "deepseek/", "meta-llama/", "mistralai/", "google/", "qwen/", "zhipu-ai/", "nousresearch/", "nvidia/", "moonshotai/")):
            return "huggingface"

    return "openrouter"


def build_llm_request(provider, model_id):
    """Возвращает url/headers для провайдера. Не логирует ключи."""
    if provider == "mistral":
        if not settings.MISTRAL_API_KEY:
            return None, None
        return (
            f"{settings.MISTRAL_BASE_URL}/chat/completions",
            {"Authorization": f"Bearer {settings.MISTRAL_API_KEY}", "Content-Type": "application/json"},
        )

    if provider == "huggingface":
        if not settings.HUGGINGFACE_API_KEY:
            return None, None
        return (
            "https://router.huggingface.co/v1/chat/completions",
            {"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}", "Content-Type": "application/json"},
        )

    # OpenRouter fallback.
    if not settings.OPENROUTER_API_KEY:
        return None, None
    return (
        f"{settings.OPENROUTER_BASE_URL}/chat/completions",
        {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/gizmo1h5st-png/ai-agents-telegram",
            "X-Title": "AI Agents Telegram",
        },
    )


def call_llm_sync(system_prompt, messages, task, model):
    # Убираем дубли, сохраняя порядок.
    models_to_try = []
    for m in [model] + FALLBACK_MODELS:
        if m and m not in models_to_try:
            models_to_try.append(m)

    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ЗАДАЧА: {task}"},
        *messages,
    ]

    last_error = None
    for try_model in models_to_try[:8]:
        provider = get_provider_for_model(try_model)
        url, headers = build_llm_request(provider, try_model)
        if not url:
            continue

        payload = {
            "model": try_model,
            "messages": full_messages,
            "max_tokens": settings.MAX_TOKENS_PER_REQUEST,
            "temperature": 0.7,
        }

        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, headers=headers, json=payload)

                if resp.status_code in (401, 403):
                    logger.warning(f"LLM auth error: provider={provider}, model={try_model}, status={resp.status_code}")
                    last_error = f"auth {provider} {resp.status_code}"
                    continue

                if resp.status_code in (429, 402, 404):
                    logger.warning(f"LLM fallback: provider={provider}, model={try_model}, status={resp.status_code}")
                    last_error = f"fallback {provider} {resp.status_code}"
                    time.sleep(1.5)
                    continue

                if resp.status_code != 200:
                    logger.warning(f"LLM error: provider={provider}, model={try_model}, status={resp.status_code}, body={resp.text[:200]}")
                    last_error = f"error {provider} {resp.status_code}"
                    continue

                data = resp.json()
                if "choices" not in data or not data["choices"]:
                    last_error = f"empty choices {provider}"
                    continue

                content = data["choices"][0].get("message", {}).get("content")
                if content:
                    logger.info(f"LLM success: provider={provider}, model={try_model}")
                    return content.strip()

        except Exception as e:
            logger.warning(f"LLM exception: provider={provider}, model={try_model}, error={str(e)[:120]}")
            last_error = str(e)[:120]
            continue

    logger.error(f"All LLM providers failed. Last error: {last_error}")
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
            elif c == "agents":
                await self._show_agents_dashboard(cid)
            elif c == "providers":
                await self._show_providers_help(cid)
            elif c == "help":
                await self._show_help(cid)
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

        @self.router.callback_query(F.data.startswith("agentcfg:"))
        async def agentcfg_cb(cb: CallbackQuery):
            r = cb.data.split(":", 1)[1]
            if r not in AGENT_BOTS:
                await cb.answer("❌")
                return
            await self._show_agent_card(cb.message.chat.id, r)
            await cb.answer()

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
        return await self._get_model_for_role(cid, self.role)

    async def _get_model_for_role(self, cid, role):
        am = await self.redis.get(f"agent_model:{cid}:{role}")
        if am:
            return am.decode()
        gm = await self.redis.get(f"global_model:{cid}")
        if gm:
            return gm.decode()
        return settings.get_agent_model(role)

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
        # Дедупликация должна быть на уровне конкретного бота.
        # Если ключ общий для всех 4 ботов, один бот может "съесть" сообщение,
        # адресованное другому боту.
        if await self.redis.exists(f"dd:{self.role}:{cid}:{mh}"):
            return
        await self.redis.setex(f"dd:{self.role}:{cid}:{mh}", 60, "1")
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
                active = await self.redis.get(f"active_task:{cid}")
                if active:
                    await self._clear_task_runtime_keys(cid, int(active.decode()))
                else:
                    await self.redis.delete(f"active_task:{cid}")
                await self.bot.send_message(cid, "🛑 Остановлено. Все pending-ходы очищены.")
            return

        # Пользователь может вмешаться в ход обсуждения и дать замечание конкретному агенту.
        # Форматы: ответом на сообщение бота, через @username или по роли в начале сообщения.
        if is_human:
            target_role = await self._detect_feedback_target(message)
            if target_role == self.role:
                await self._handle_human_feedback(message)
                return

    async def _detect_feedback_target(self, message: Message):
        """Понимает, адресовано ли человеческое замечание этому/другому агенту."""
        text = message.text or ""

        # Если пользователь ответил reply на сообщение этого бота — замечание точно для него.
        if message.reply_to_message and message.reply_to_message.from_user:
            if message.reply_to_message.from_user.id == await self._get_my_id():
                return self.role

        return detect_addressed_agent(text)

    async def _handle_human_feedback(self, message: Message):
        """Сохраняет замечание пользователя в историю и запускает ответ адресованного агента."""
        cid = message.chat.id
        text = message.text or ""
        active = await self.redis.get(f"active_task:{cid}")

        if not active:
            await self.bot.send_message(
                cid,
                f"{self.config['emoji']} Принял замечание, но сейчас нет активной задачи. "
                f"Запусти обсуждение через: <code>Задача: описание</code>",
                parse_mode="HTML"
            )
            return

        tid = int(active.decode())
        task_raw = await self.redis.get(f"task_desc:{cid}:{tid}")
        td = task_raw.decode() if task_raw else ""
        feedback = clean_feedback_text(text)
        user_name = "Пользователь"
        if message.from_user:
            user_name = message.from_user.full_name or message.from_user.username or "Пользователь"

        note = (
            f"Замечание пользователя для агента {self.config['name']} ({self.role}).\n"
            f"Автор: {user_name}.\n"
            f"Текст замечания: {feedback}\n\n"
            f"Инструкция агенту: обязательно учти это замечание, пересмотри свой предыдущий вывод "
            f"и при необходимости измени точку зрения. Если пользователь прав — признай это. "
            f"Не спорь ради спора и не игнорируй обратную связь."
        )
        await self._save_message(cid, tid, "Пользователь", note)

        # Даём адресованному агенту ближайший ход.
        await self.redis.setex(f"turn:{cid}:{tid}", 600, self.role)

        # Если лимит шагов уже достигнут, откатываем счётчик на 1, чтобы агент мог ответить на замечание.
        sr = await self.redis.get(f"steps:{cid}:{tid}")
        ms = await self._get_max_steps(cid)
        steps = int(sr) if sr else 0
        if steps >= ms and ms > 0:
            await self.redis.setex(f"steps:{cid}:{tid}", 7200, str(ms - 1))

        await self.redis.setex(f"pending:{cid}:{self.role}", 300, f"{tid}:{td}")
        await self.bot.send_message(
            cid,
            f"{self.config['emoji']} Принял замечание и пересмотрю позицию с учётом вашей правки."
        )

    async def _show_menu(self, cid):
        gmr = await self.redis.get(f"global_model:{cid}")
        gm = (gmr.decode() if gmr else settings.DEFAULT_MODEL).split("/")[-1].replace(":free", "")
        ms = await self._get_max_steps(cid)
        dl = await self._get_delay(cid)
        active = await self.redis.get(f"active_task:{cid}")
        status = "🟢 активна" if active else "⚪ нет активной задачи"

        btns = [
            [InlineKeyboardButton(text="👥 Агенты", callback_data="cmd:agents"), InlineKeyboardButton(text="🎛 Модели агентов", callback_data="cmd:agentmodel")],
            [InlineKeyboardButton(text="🤖 Общая модель", callback_data="cmd:model"), InlineKeyboardButton(text="📋 Все модели", callback_data="cmd:models")],
            [InlineKeyboardButton(text="📊 Шаги", callback_data="cmd:steps"), InlineKeyboardButton(text="⏱ Задержка", callback_data="cmd:delay")],
            [InlineKeyboardButton(text="🧩 Free API провайдеры", callback_data="cmd:providers"), InlineKeyboardButton(text="⚙️ Конфиг", callback_data="cmd:config")],
            [InlineKeyboardButton(text="❓ Как пользоваться", callback_data="cmd:help"), InlineKeyboardButton(text="🔄 Сброс моделей", callback_data="resetmodels")],
        ]

        text = (
            "<b>🚀 AI Agents Team</b>\n"
            "<i>4 Telegram-агента: координатор, исследователь, критик, исполнитель.</i>\n\n"
            f"<b>Состояние:</b> {status}\n"
            f"<b>Общая модель:</b> <code>{gm}</code>\n"
            f"<b>Лимит:</b> {ms} шагов · <b>Пауза:</b> {dl}с\n\n"
            "<b>Быстрый старт:</b>\n"
            "• <code>Задача: опиши задачу</code> — начать обсуждение\n"
            "• <code>/stop</code> — остановить активную задачу\n"
            "• Замечание агенту: ответь reply на сообщение бота или напиши <code>@Researcher1_ai_bot текст</code>\n\n"
            "<b>Правильные usernames:</b>\n"
            "🎯 <code>@coordintor_ai_bot</code>\n"
            "🔍 <code>@Researcher1_ai_bot</code>\n"
            "🧐 <code>@criticaibot_bot</code>\n"
            "⚡ <code>@executorai_ai_bot</code>"
        )
        await self.bot.send_message(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_agents_dashboard(self, cid):
        btns = []
        for r in ROLE_ORDER:
            cfg = AGENT_BOTS[r]
            model = await self._get_model_for_role(cid, r)
            short = model.split("/")[-1].replace(":free", "")[:22]
            btns.append([InlineKeyboardButton(text=f"{cfg['emoji']} {cfg['name']} · {short}", callback_data=f"agentcfg:{r}")])
        btns.append([InlineKeyboardButton(text="🎛 Быстрая смена моделей", callback_data="cmd:agentmodel")])
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:help")])
        await self.bot.send_message(
            cid,
            "👥 <b>Настройки агентов</b>\n\nВыбери агента, чтобы посмотреть роль, username и сменить модель:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
        )

    async def _show_agent_card(self, cid, role):
        cfg = AGENT_BOTS[role]
        model = await self._get_model_for_role(cid, role)
        username = {
            "coordinator": "@coordintor_ai_bot",
            "researcher": "@Researcher1_ai_bot",
            "critic": "@criticaibot_bot",
            "executor": "@executorai_ai_bot",
        }.get(role, "")
        desc = {
            "coordinator": "управляет ходом обсуждения и финализирует ответ",
            "researcher": "ищет факты, делает краткий анализ, учитывает ваши поправки",
            "critic": "проверяет логику, риски и слабые места",
            "executor": "делает практический результат: код, текст, план, расчёты",
        }.get(role, "")
        btns = [
            [InlineKeyboardButton(text="🤖 Сменить модель", callback_data=f"pickagent:{role}")],
            [InlineKeyboardButton(text="👥 Все агенты", callback_data="cmd:agents"), InlineKeyboardButton(text="🏠 Помощь", callback_data="cmd:help")],
        ]
        text = (
            f"{cfg['emoji']} <b>{cfg['name']}</b>\n\n"
            f"<b>Username:</b> <code>{username}</code>\n"
            f"<b>Роль:</b> {desc}\n"
            f"<b>Модель:</b> <code>{model}</code>\n\n"
            "<b>Как дать замечание:</b>\n"
            "1. Ответь reply на сообщение этого бота; или\n"
            f"2. Напиши: <code>{username} твоё замечание</code>\n\n"
            "Агент получит ближайший ход и обязан пересмотреть позицию."
        )
        await self.bot.send_message(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_help(self, cid):
        text = (
            "❓ <b>Как пользоваться AI Agents Team</b>\n\n"
            "<b>1. Запуск задачи</b>\n"
            "<code>Задача: придумай план запуска Telegram SaaS</code>\n\n"
            "<b>2. Вмешательство в обсуждение</b>\n"
            "Если не согласен с агентом — ответь reply на его сообщение или напиши username:\n"
            "<code>@Researcher1_ai_bot ты не учёл лимиты бесплатных API</code>\n\n"
            "<b>3. Управление</b>\n"
            "<code>/stop</code> — остановить\n"
            "<code>/steps</code> — лимит шагов\n"
            "<code>/delay</code> — задержка между агентами\n"
            "<code>/agentmodel</code> — модель для каждого агента\n\n"
            "Совет: для бесплатных API ставь 8–12 шагов и задержку 8–15 секунд."
        )
        btns = [[InlineKeyboardButton(text="👥 Агенты", callback_data="cmd:agents"), InlineKeyboardButton(text="⚙️ Конфиг", callback_data="cmd:config")]]
        await self.bot.send_message(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

    async def _show_providers_help(self, cid):
        text = (
            "🧩 <b>Бесплатные API-провайдеры</b>\n\n"
            "Полного легального безлимита у hosted LLM API почти не бывает. Реальный вариант — несколько permanent free tiers + fallback.\n\n"
            "<b>Сейчас подключено в коде:</b>\n"
            "1. <b>Mistral API direct</b> — основной провайдер через MISTRAL_API_KEY.\n"
            "2. <b>OpenRouter</b> — fallback, если есть OPENROUTER_API_KEY.\n"
            "3. <b>HuggingFace</b> — fallback, если есть HUGGINGFACE_API_KEY.\n\n"
            "Для бесплатного режима ставь 8–12 шагов, задержку 8–15с и Mistral Small / Nemo для агентов."
        )
        await self.bot.send_message(cid, text, parse_mode="HTML")

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
        t = "📋 <b>Модели:</b>\n"
        for provider_name, provider_key in [("Mistral API", "mistral"), ("OpenRouter", "openrouter"), ("HuggingFace", "huggingface")]:
            t += f"\n<b>{provider_name}:</b>\n"
            found = False
            for k, m in FREE_MODELS.items():
                if m.get("provider") == provider_key:
                    t += f"• <code>{k}</code> — {m['name']}\n"
                    found = True
            if not found:
                t += "• нет моделей\n"
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

    async def _clear_task_runtime_keys(self, cid, tid):
        """Удаляет все runtime-ключи задачи, чтобы старые pending не крутили завершённую задачу."""
        await self.redis.delete(f"active_task:{cid}")
        await self.redis.delete(f"turn:{cid}:{tid}")
        await self.redis.delete(f"final_reason:{cid}:{tid}")
        await self.redis.delete(f"llm_fail:{cid}:{tid}")
        await self.redis.delete(f"finalizing:{cid}:{tid}")
        for role in ROLE_ORDER:
            await self.redis.delete(f"pending:{cid}:{role}")
            await self.redis.delete(f"rate:{role}:{cid}")

    async def _complete_task(self, cid, tid, completion_message="✅ Задача завершена. Новые ходы агентов остановлены."):
        """Единая точка завершения задачи."""
        await self._clear_task_runtime_keys(cid, tid)
        if completion_message:
            try:
                await self.bot.send_message(cid, completion_message)
            except Exception:
                pass

    async def _force_finalize(self, cid, tid, td, reason="Достигнут лимит обсуждения."):
        """Принудительно завершает задачу, чтобы обсуждение не зависало бесконечно."""
        lock_key = f"finalizing:{cid}:{tid}"
        got_lock = await self.redis.set(lock_key, self.role, nx=True, ex=300)
        if not got_lock:
            return

        try:
            history = await self._get_history(cid, tid)
            llm_msgs = [
                {"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]}
                for m in history[-12:]
            ]
            prompt = AGENT_BOTS["coordinator"]["prompt"] + CORRECT_USERNAMES_PROMPT + FINALIZATION_PROMPT
            model = await self._get_model_for_role(cid, "coordinator")
            response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, td, model)

            if not response:
                short_history = "\n".join([m.get("content", "")[:500] for m in history[-8:]])
                response = (
                    "[ФИНАЛЬНЫЙ ОТВЕТ]\n"
                    f"Обсуждение завершено принудительно. Причина: {reason}\n\n"
                    f"Задача: {td}\n\n"
                    "Краткий итог по последним сообщениям команды:\n"
                    f"{short_history if short_history else 'История обсуждения пуста или недоступна.'}\n\n"
                    "Рекомендация: использовать этот итог как черновик и при необходимости запустить новую задачу с уточнениями."
                )

            response = normalize_agent_mentions(response)
            if not is_final_response(response):
                response = "[ФИНАЛЬНЫЙ ОТВЕТ]\n" + response

            msg = f"🎯 {response}"
            if len(msg) > 4000:
                msg = msg[:4000] + "..."
            await self.bot.send_message(cid, msg)
            await self._save_message(cid, tid, "Координатор", msg)
            await self._complete_task(cid, tid, "✅ Завершено. Задача закрыта, дальнейшие ходы остановлены.")
        except Exception as e:
            logger.error(f"Force finalize error: {e}")
            await self._clear_task_runtime_keys(cid, tid)
            try:
                await self.bot.send_message(cid, f"✅ Обсуждение остановлено по лимиту/ошибке. Причина: {str(e)[:120]}")
            except Exception:
                pass

    async def _redirect_to_coordinator_for_final(self, cid, tid, td, reason="Достигнут лимит обсуждения."):
        """Передаёт финализацию координатору, если текущий агент не координатор."""
        await self.redis.setex(f"turn:{cid}:{tid}", 600, "coordinator")
        await self.redis.setex(f"final_reason:{cid}:{tid}", 600, reason)
        await self.redis.setex(f"pending:{cid}:coordinator", 300, f"{tid}:{td}")

    async def _think_and_reply(self, cid, tid, td, hist, steps):
        active = await self.redis.get(f"active_task:{cid}")
        if not active or active.decode() != str(tid):
            await self._clear_task_runtime_keys(cid, tid)
            return

        step = steps + 1
        prompt = self.config["prompt"] + CORRECT_USERNAMES_PROMPT
        ms = await self._get_max_steps(cid)

        if self.role == "coordinator" and step < 5:
            prompt += f"\n\nШаг {step}/{ms}. РАНО для финального ответа."
        elif self.role == "coordinator" and step >= max(6, ms - 2):
            prompt += FINALIZATION_PROMPT
        elif self.role == "coordinator" and step >= 6:
            prompt += "\n\nЕсли данных уже достаточно, заверши обсуждение через [ФИНАЛЬНЫЙ ОТВЕТ]. Не растягивай диалог без необходимости."

        final_reason = await self.redis.get(f"final_reason:{cid}:{tid}")
        if final_reason and self.role == "coordinator":
            prompt += FINALIZATION_PROMPT

        llm_msgs = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in hist[-10:]]
        model = await self._get_model(cid)
        response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, td, model)
        if not response:
            fail_key = f"llm_fail:{cid}:{tid}"
            fails = await self.redis.incr(fail_key)
            await self.redis.expire(fail_key, 900)
            logger.warning(f"LLM empty response: role={self.role}, task={tid}, fails={fails}")

            if self.role == "coordinator" and (fails >= 2 or step >= ms):
                await self._force_finalize(cid, tid, td, "Модель не вернула ответ или исчерпан лимит шагов.")
                return

            if fails >= 2 or step >= ms:
                await self._redirect_to_coordinator_for_final(cid, tid, td, "Модель не вернула ответ или исчерпан лимит шагов.")
                return

            idx = ROLE_ORDER.index(self.role) if self.role in ROLE_ORDER else 0
            na = ROLE_ORDER[(idx + 1) % len(ROLE_ORDER)]
            await self.redis.setex(f"turn:{cid}:{tid}", 600, na)
            await self.redis.setex(f"pending:{cid}:{na}", 300, f"{tid}:{td}")
            return

        await self.redis.delete(f"llm_fail:{cid}:{tid}")
        response = normalize_agent_mentions(response)
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
        new_steps = await self.redis.incr(f"steps:{cid}:{tid}")
        await self._save_message(cid, tid, self.config["name"], msg)
        if is_final_response(response):
            await self._complete_task(cid, tid, "✅ Финальный ответ получен. Задача закрыта, дальнейшие ходы остановлены.")
            return

        if int(new_steps) >= ms:
            if self.role == "coordinator":
                await self._force_finalize(cid, tid, td, "Достигнут лимит шагов обсуждения.")
            else:
                await self._redirect_to_coordinator_for_final(cid, tid, td, "Достигнут лимит шагов обсуждения.")
            return

        na = detect_next_agent(response, current_role=self.role)
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
                    if not active or active.decode() != str(tid):
                        await self._clear_task_runtime_keys(cid, tid)
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
                        if self.role == "coordinator":
                            await self._force_finalize(cid, tid, td, "Достигнут лимит шагов обсуждения.")
                        else:
                            await self._redirect_to_coordinator_for_final(cid, tid, td, "Достигнут лимит шагов обсуждения.")
                        continue
                    await self._think_and_reply(cid, tid, td, history, steps)
            except Exception as e:
                logger.error(f"Poll error: {e}")

    async def stop(self):
        await self.bot.session.close()
