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
from app.llm.router import call_llm_sync, get_provider_for_model, get_llm_router_status
from app.db.crud import create_task, add_message, update_task_status, update_task
from app.db.models import TaskStatus
from app.run_journal import add_run_event, get_run_events, create_plan_for_team, save_run_plan, get_run_plan, mark_plan_role_done, format_plan, format_events
from app.skills.loader import list_skills, select_skills_for_task, build_skills_context, read_context_files
from app.memory.service import remember, list_chat_memories, search_chat_memories, clear_chat_memories, build_memory_context, save_task_lesson, format_memories
from app.artifacts import extract_artifacts_from_text, save_artifacts, load_artifacts, clear_artifacts, format_artifacts
from app.github_service import publish_task_artifacts
from app.github_publisher import GitHubPublisherError, GitHubConflictError

logger = logging.getLogger(__name__)
ROLE_ORDER = ["coordinator", "researcher", "architect", "executor", "qa", "critic"]
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

SEARCH_CACHE = {}
SEARCH_CACHE_TTL = 60 * 30

TASK_STEP_BUDGETS = {
    "simple_artifact": {"min": 3, "soft": 5, "hard": 8},
    "simple_answer": {"min": 2, "soft": 4, "hard": 6},
    "general": {"min": 5, "soft": 10, "hard": 16},
    "research": {"min": 6, "soft": 12, "hard": 18},
    "debug": {"min": 6, "soft": 12, "hard": 20},
    "architecture": {"min": 10, "soft": 18, "hard": 28},
}

TASK_TEAMS = {
    "simple_artifact": ["coordinator", "executor", "qa"],
    "simple_answer": ["coordinator", "executor", "critic"],
    "general": ["coordinator", "researcher", "executor", "critic"],
    "research": ["coordinator", "researcher", "critic"],
    "debug": ["coordinator", "researcher", "executor", "qa", "critic"],
    "architecture": ["coordinator", "researcher", "architect", "executor", "qa", "critic"],
}


def classify_task(task: str) -> str:
    t = (task or "").lower()
    if any(x in t for x in ["создай файл", "создать файл", "подготовь файл", "сгенерируй файл", "перезаписать", "перезапиши", "generated_code", "[file:", "github", "push", "артефакт", "docs/", ".md", ".html", ".css", ".json", ".yaml", ".yml", "hello world"]):
        return "simple_artifact"
    if any(x in t for x in ["коротко", "ответь", "объясни кратко", "простыми словами"]):
        return "simple_answer"
    if any(x in t for x in ["ошибка", "лог", "railway", "redis", "postgres", "telegramconflict", "не запускается", "падает", "debug"]):
        return "debug"
    if any(x in t for x in ["архитект", "спроектируй", "масштаб", "инфраструкт", "api", "saas", "микросервис"]):
        return "architecture"
    if any(x in t for x in ["найди", "исследуй", "рынок", "конкурент", "документац", "сравни"]):
        return "research"
    return getattr(settings, "DEFAULT_TASK_TYPE", "general") or "general"


def budget_for_task_type(task_type: str) -> dict:
    return TASK_STEP_BUDGETS.get(task_type, TASK_STEP_BUDGETS["general"])


def allowed_user_id(uid):
    if uid is None:
        return False
    if not settings.allowed_user_ids:
        return True
    return uid in settings.allowed_user_ids


AGENT_MENTIONS = {
    "coordinator": ("@coordintor_ai_bot", "@coordinator_ai_bot"),
    "researcher": ("@researcher1_ai_bot",),
    "architect": ("@architect1_ai_bot",),
    "executor": ("@executorai_ai_bot",),
    "qa": ("@qabotai_bot",),
    "critic": ("@criticaibot_bot",),
}
AGENT_NAME_PATTERNS = {
    "coordinator": (r"\bкоординатор(?:у|а|ом|е)?\b",),
    "researcher": (r"\bисследователь(?:ю|я|ем|е)?\b",),
    "architect": (r"\bархитектор(?:у|а|ом|е)?\b|\barchitect\b",),
    "executor": (r"\bисполнитель(?:ю|я|ем|е)?\b",),
    "qa": (r"\bqa\b|\bтестировщик(?:у|а|ом|е)?\b|\bтестер(?:у|а|ом|е)?\b",),
    "critic": (r"\bкритик(?:у|а|ом|е)?\b",),
}
TURN_MARKERS = (
    "передаю", "передать", "передай", "слово", "следующий", "следующая",
    "пусть", "обратимся", "назначаю", "вызываю", "далее"
)

CORRECT_USERNAMES_PROMPT = """

ВАЖНО: правильные usernames агентов в Telegram:
- Координатор: @coordintor_ai_bot
- Исследователь: @Researcher1_ai_bot
- Архитектор: @Architect1_ai_bot
- Исполнитель: @executorai_ai_bot
- QA: @Qabotai_bot
- Критик: @criticaibot_bot

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

STRUCTURED_OUTPUT_PROMPT = """

СТРОГИЙ ФОРМАТ ОТВЕТА:
Отвечай ТОЛЬКО валидным JSON без Markdown, без ``` и без текста до/после JSON.
Схема:
{
  "message": "текст сообщения, который увидит пользователь",
  "next_agent": "coordinator|researcher|architect|executor|qa|critic|null",
  "final": false
}

Правила JSON:
- message: только текст твоего ответа, БЕЗ JSON, БЕЗ обращения к самому себе, БЕЗ ```json.
- next_agent: следующий агент, если final=false. Нельзя указывать самого себя.
- final: true только если это финальный ответ по задаче.
- Если final=true, message ОБЯЗАТЕЛЬНО начинается с [ФИНАЛЬНЫЙ ОТВЕТ], а next_agent должен быть null.
- Если final=false, next_agent должен быть одним из ДРУГИХ агентов.
- Не добавляй реплики от имени других агентов.
- Не начинай message с названия своей роли, например "Критик, ..." или "Исследователь, ...".
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
    cleaned = re.sub(r"^\s*(координатор|исследователь|архитектор|исполнитель|qa|тестировщик|критик)[\s,.:;!—-]+", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or (text or "").strip()


FINAL_PATTERNS = (
    r"\[\s*финальн(?:ый|ая|ое)\s+ответ\s*\]",
    r"\[\s*final\s*\]",
    r"^\s*финальн(?:ый|ая|ое)\s+ответ\s*[:：]",
    r"^\s*итогов(?:ый|ая|ое)\s+ответ\s*[:：]",
    r"^\s*окончательн(?:ый|ая|ое)\s+ответ\s*[:：]",
    r"обсуждение\s+завершено",
    r"задача\s+завершена",
)


def is_final_response(text):
    """Распознаёт финальный ответ в разных форматах, а не только строго [ФИНАЛЬНЫЙ ОТВЕТ]."""
    t = (text or "").strip().lower()
    return any(re.search(pattern, t, flags=re.IGNORECASE | re.MULTILINE) for pattern in FINAL_PATTERNS)


def strip_final_markers(text):
    """Убирает финальные маркеры, если модель попыталась завершить слишком рано."""
    t = text or ""
    t = re.sub(r"\[\s*финальн(?:ый|ая|ое)\s+ответ\s*\]", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\[\s*final\s*\]", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*финальн(?:ый|ая|ое)\s+ответ\s*[:：]", "", t, flags=re.IGNORECASE | re.MULTILINE)
    t = re.sub(r"^\s*итогов(?:ый|ая|ое)\s+ответ\s*[:：]", "", t, flags=re.IGNORECASE | re.MULTILINE)
    t = re.sub(r"^\s*окончательн(?:ый|ая|ое)\s+ответ\s*[:：]", "", t, flags=re.IGNORECASE | re.MULTILINE)
    return t.strip()


def is_incomplete_final_response(text):
    """True, если модель поставила финальный маркер, но по смыслу продолжает процесс."""
    t = (text or "").lower()
    markers = (
        "требуется", "нужно", "необходимо", "осталось", "переходим",
        "передаю", "требует", "финальное согласование", "этап валидации",
        "валидац", "согласован", "уточнить", "уточни", "подтверди",
        "доработать", "проверить", "проверь", "выполни", "предоставь",
        "оставшимся", "оставшиеся", "следует"
    )
    if not any(m in t for m in markers):
        return False
    agent_mentioned = any(
        m in t
        for role in ROLE_ORDER if role != "coordinator"
        for m in AGENT_MENTIONS.get(role, ())
    ) or any(
        str(AGENT_BOTS.get(role, {}).get("name", "")).lower() in t
        for role in ROLE_ORDER if role != "coordinator"
    )
    hard = ("финальное согласование", "переходим", "этап валидации", "осталось", "оставшимся", "оставшиеся")
    return agent_mentioned or any(h in t for h in hard)


def asks_another_agent_to_continue(text, current_role="coordinator"):
    """Detects pseudo-final answers that actually delegate work to another agent."""
    t = (text or "").lower()
    action_words = (
        "подтверди", "уточни", "проверь", "выполни", "сделай", "предоставь",
        "проанализируй", "оцени", "передаю", "переходим", "нужно", "требуется",
        "запроси", "исправь", "доработай", "валидируй", "проведи"
    )
    if not any(w in t for w in action_words):
        return False
    for role in ROLE_ORDER:
        if role == current_role:
            continue
        if any(m in t for m in AGENT_MENTIONS.get(role, ())) or str(AGENT_BOTS.get(role, {}).get("name", "")).lower() in t:
            return True
    return False


def _extract_json_object(raw):
    """Достаёт JSON-объект из ответа модели, даже если модель обернула его в ```json."""
    if not raw:
        return None
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.IGNORECASE).strip()
    txt = re.sub(r"\s*```$", "", txt).strip()
    if txt.startswith("{") and txt.endswith("}"):
        return txt
    m = re.search(r"\{[\s\S]*\}", txt)
    return m.group(0) if m else None


def _strip_code_fences(text):
    if not text:
        return text
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s*```$", "", t).strip()
    return t


def _extract_json_string_field(raw, field):
    """Достаёт строковое поле из JSON даже если JSON обрезан/невалиден."""
    if not raw:
        return None
    m = re.search(r'"' + re.escape(field) + r'"\s*:\s*"', raw)
    if not m:
        return None
    i = m.end()
    out = []
    esc = False
    while i < len(raw):
        ch = raw[i]
        if esc:
            if ch == "n":
                out.append("\n")
            elif ch == "t":
                out.append("\t")
            elif ch == "r":
                out.append("\r")
            elif ch in ('"', "\\", "/"):
                out.append(ch)
            else:
                out.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            return "".join(out).strip()
        else:
            out.append(ch)
        i += 1
    return "".join(out).strip() if out else None


def _extract_json_bool_field(raw, field):
    m = re.search(r'"' + re.escape(field) + r'"\s*:\s*(true|false)', raw or "", flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower() == "true"


def sanitize_visible_agent_message(message, current_role=None):
    """Чистит то, что будет показано в Telegram: убирает JSON/code-fence мусор и самообращение."""
    msg = _strip_code_fences(message or "").strip()

    # Если в fallback попал почти-JSON, пытаемся вытащить message.
    extracted = _extract_json_string_field(msg, "message")
    if extracted:
        msg = extracted.strip()

    # Убираем грубое обращение агента к самому себе в начале ответа.
    if current_role:
        names = {
            "coordinator": ["координатор", "@coordintor_ai_bot", "@coordinator_ai_bot"],
            "researcher": ["исследователь", "@researcher1_ai_bot"],
            "architect": ["архитектор", "@architect1_ai_bot"],
            "executor": ["исполнитель", "@executorai_ai_bot"],
            "qa": ["qa", "тестировщик", "тестер", "@qabotai_bot"],
            "critic": ["критик", "@criticaibot_bot"],
        }.get(current_role, [])
        for name in names:
            msg = re.sub(rf"^\s*{re.escape(name)}\s*(?:\([^)]*\))?\s*[,：:;—-]+\s*", "", msg, flags=re.IGNORECASE)

    return normalize_agent_mentions(msg).strip()


def parse_structured_agent_response(raw, current_role=None):
    """Возвращает (message, next_agent, final, parsed_ok). Совместим со старым текстовым форматом."""
    raw = normalize_agent_mentions(raw or "")
    cleaned_raw = _strip_code_fences(raw)
    obj_txt = _extract_json_object(cleaned_raw)

    if obj_txt:
        try:
            data = json.loads(obj_txt)
            message = sanitize_visible_agent_message(str(data.get("message", "")).strip(), current_role=current_role)
            next_agent = data.get("next_agent", None)
            final = bool(data.get("final", False))

            if isinstance(next_agent, str):
                next_agent = next_agent.strip().lower()
                if next_agent in ("", "none", "null", "-", "нет"):
                    next_agent = None
            else:
                next_agent = None

            if next_agent not in ROLE_ORDER:
                next_agent = None
            if next_agent == current_role:
                next_agent = None

            if not message:
                message = sanitize_visible_agent_message(cleaned_raw, current_role=current_role)
            if is_final_response(message):
                final = True
                next_agent = None
            if final:
                next_agent = None
                if not is_final_response(message):
                    message = "[ФИНАЛЬНЫЙ ОТВЕТ]\n" + message

            return message, next_agent, final, True
        except Exception as e:
            logger.warning(f"Structured JSON parse failed: {str(e)[:120]}")

    # Partial JSON fallback: если модель начала ```json {"message": ... и не закрыла JSON.
    partial_message = _extract_json_string_field(cleaned_raw, "message")
    partial_next = _extract_json_string_field(cleaned_raw, "next_agent")
    partial_final = _extract_json_bool_field(cleaned_raw, "final")
    if partial_message:
        message = sanitize_visible_agent_message(partial_message, current_role=current_role)
        next_agent = (partial_next or "").strip().lower() or None
        if next_agent in ("none", "null", "-", "нет"):
            next_agent = None
        if next_agent not in ROLE_ORDER or next_agent == current_role:
            next_agent = None
        final = bool(partial_final) or is_final_response(message)
        if final:
            next_agent = None
            if not is_final_response(message):
                message = "[ФИНАЛЬНЫЙ ОТВЕТ]\n" + message
        return message, next_agent, final, False

    # Backward-compatible fallback: старый свободный текст, но без code fences.
    message = sanitize_visible_agent_message(cleaned_raw, current_role=current_role)
    final = is_final_response(message)
    next_agent = None if final else detect_next_agent(message, current_role=current_role)
    return message, next_agent, final, False


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


def search_web(query):
    q = (query or "").strip()
    if not q:
        return ""
    ck = q.lower()
    cached = SEARCH_CACHE.get(ck)
    if cached and (time.time() - cached[0]) < SEARCH_CACHE_TTL:
        return cached[1]
    try:
        from ddgs import DDGS
        r = []
        with DDGS() as d:
            for x in d.text(q, max_results=3):
                title = x.get('title', '')
                body = x.get('body', '')
                href = x.get('href') or x.get('url') or ''
                r.append(f"- {title}: {body}\n  {href}")
        result = "\n".join(r) if r else ""
        SEARCH_CACHE[ck] = (time.time(), result)
        if len(SEARCH_CACHE) > 100:
            for k in list(SEARCH_CACHE.keys())[:40]:
                SEARCH_CACHE.pop(k, None)
        return result
    except Exception as e:
        logger.warning(f"Search error: {str(e)[:120]}")
        return ""


def required_roles_before_final():
    raw = getattr(settings, "REQUIRED_ROLES_BEFORE_FINAL", "researcher,architect,executor,qa,critic")
    return [r.strip() for r in raw.split(",") if r.strip() and r.strip() in ROLE_ORDER]


PROMPT_VARIANTS = {
    "coordinator": {
        "balanced": {"name": "⚖️ Баланс", "suffix": "Координируй спокойно: план → сбор фактов → реализация → проверка → финал. Не спеши с финалом."},
        "deep": {"name": "🧠 Глубокий", "suffix": "Требуй от команды глубокий анализ, альтернативы, риски и аргументы. Финализируй только после QA и Критика."},
        "fast": {"name": "⚡ Быстрый", "suffix": "Минимизируй круги обсуждения, но не пропускай обязательные роли активной команды. Итог краткий."},
        "strict": {"name": "🧩 Строгий", "suffix": "Жёстко следи за порядком, активной командой, JSON-форматом и запретом раннего финала."},
        "creative": {"name": "💡 Креатив", "suffix": "Поощряй нестандартные варианты, но проси Критика и QA отфильтровать рискованные идеи."},
    },
    "researcher": {
        "balanced": {"name": "⚖️ Баланс", "suffix": "Давай факты и краткие выводы без воды. Передавай архитектурные вопросы Архитектору."},
        "deep": {"name": "🔬 Deep research", "suffix": "Ищи причины, ограничения, аналоги, неизвестные и риски. Явно отмечай уверенность и пробелы."},
        "brief": {"name": "📝 Кратко", "suffix": "Отвечай максимум 3-5 пунктами. Только самое важное для следующего агента."},
        "market": {"name": "📊 Рынок", "suffix": "Фокусируйся на аналогах, конкурентах, трендах, бизнес-контексте и практической применимости."},
        "technical": {"name": "🛠 Техфакты", "suffix": "Фокусируйся на технических фактах, ограничениях, протоколах, API, инфраструктуре и данных."},
    },
    "architect": {
        "balanced": {"name": "⚖️ Баланс", "suffix": "Проектируй понятную архитектуру: компоненты, связи, данные, риски и компромиссы."},
        "enterprise": {"name": "🏢 Enterprise", "suffix": "Думай как enterprise architect: безопасность, масштабирование, наблюдаемость, SLA, интеграции, сопровождение."},
        "startup": {"name": "🚀 MVP", "suffix": "Проектируй MVP-архитектуру: быстро, дёшево, просто, с возможностью масштабирования позже."},
        "cloud": {"name": "☁️ Cloud", "suffix": "Фокус на cloud-native: сервисы, очереди, кэш, БД, CI/CD, мониторинг, отказоустойчивость."},
        "minimal": {"name": "📦 Минимализм", "suffix": "Предлагай максимально простую архитектуру без лишних компонентов. Обосновывай, что можно не делать."},
    },
    "executor": {
        "balanced": {"name": "⚖️ Баланс", "suffix": "Делай практичный результат: шаги, код, структуру, инструкции. После результата передай QA."},
        "code": {"name": "💻 Код", "suffix": "Фокусируйся на коде, структурах файлов, командах запуска и конкретных фрагментах реализации."},
        "plan": {"name": "📋 План", "suffix": "Давай пошаговый план реализации, чеклист и порядок внедрения."},
        "ops": {"name": "⚙️ DevOps", "suffix": "Фокус на деплое, переменных окружения, Docker/Railway, логах, мониторинге и откате."},
        "concise": {"name": "✂️ Кратко", "suffix": "Минимум рассуждений, максимум готового результата и команд."},
    },
    "qa": {
        "balanced": {"name": "⚖️ Баланс", "suffix": "Проверяй полноту, риски, edge cases и критерии приёмки. Давай короткий verdict."},
        "strict": {"name": "🧪 Строгий QA", "suffix": "Будь придирчивым: ищи блокеры, непроверенные допущения, пропущенные требования и регрессии."},
        "testcases": {"name": "✅ Тест-кейсы", "suffix": "Фокусируйся на тест-кейсах: positive, negative, edge, интеграционные и acceptance criteria."},
        "security": {"name": "🛡 QA Security", "suffix": "Проверяй безопасность, доступы, секреты, утечки, abuse-cases и privacy."},
        "ux": {"name": "👤 UX QA", "suffix": "Проверяй пользовательские сценарии, понятность, ошибки интерфейса и удобство эксплуатации."},
    },
    "critic": {
        "balanced": {"name": "⚖️ Баланс", "suffix": "Проверяй логику, риски и слабые места. Давай конструктивные улучшения."},
        "hard": {"name": "🔥 Жёсткий", "suffix": "Будь максимально строгим: ищи противоречия, слабые аргументы, завышенные обещания и скрытые риски."},
        "business": {"name": "💼 Бизнес", "suffix": "Оцени ценность, стоимость, сроки, рынок, риски внедрения и ROI."},
        "technical": {"name": "🛠 Техкритик", "suffix": "Фокусируйся на технической реализуемости, архитектурных долгах, производительности и поддержке."},
        "finalcheck": {"name": "🏁 Финальная проверка", "suffix": "Проверяй готовность к финальному ответу: все ли роли высказались, нет ли открытых вопросов."},
    },
}


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
            if cb.from_user and not allowed_user_id(cb.from_user.id):
                await cb.answer("⛔ Нет доступа", show_alert=True)
                return
            cid = cb.message.chat.id
            c = cb.data.split(":")[1]
            if c == "menu":
                await self._show_menu(cid, cb.message)
            elif c == "model":
                await self._show_model_picker(cid, cb.message)
            elif c == "agentmodel":
                await self._show_agent_model_picker(cid, cb.message)
            elif c == "agentprompts":
                await self._show_agent_prompt_picker(cid, cb.message)
            elif c == "models":
                await self._show_models_list(cid, cb.message)
            elif c == "config":
                await self._show_config(cid, cb.message)
            elif c == "agents":
                await self._show_agents_dashboard(cid, cb.message)
            elif c == "team":
                await self._show_team_picker(cid, cb.message)
            elif c == "providers":
                await self._show_providers_help(cid, cb.message)
            elif c == "skills":
                await self._show_skills(cid, cb.message)
            elif c == "context":
                await self._show_context(cid, cb.message)
            elif c == "help":
                await self._show_help(cid, cb.message)
            elif c == "steps":
                await self._show_steps_picker(cid, cb.message)
            elif c == "delay":
                await self._show_delay_picker(cid, cb.message)
            elif c == "status":
                await self._show_status(cid, cb.message)
            elif c == "history":
                await self._show_history(cid, cb.message)
            elif c == "memory":
                await self._show_memory(cid, cb.message)
            elif c == "events":
                await self._show_events(cid, cb.message)
            elif c == "artifacts":
                await self._show_artifacts(cid, cb.message)
            elif c == "github":
                await self._show_github_status(cid, cb.message)
            elif c == "plan":
                await self._show_plan(cid, cb.message)
            await cb.answer()

        @self.router.callback_query(F.data.startswith("hist:"))
        async def hist_cb(cb: CallbackQuery):
            if self.role != "coordinator":
                await cb.answer()
                return
            if cb.from_user and not allowed_user_id(cb.from_user.id):
                await cb.answer("⛔ Нет доступа", show_alert=True)
                return
            try:
                db_task_id = int(cb.data.split(":", 1)[1])
            except Exception:
                await cb.answer("❌")
                return
            await self._show_task_detail(cb.message.chat.id, db_task_id, cb.message)
            await cb.answer()

        @self.router.callback_query(F.data.startswith("task:"))
        async def task_cb(cb: CallbackQuery):
            if self.role != "coordinator":
                await cb.answer()
                return
            if cb.from_user and not allowed_user_id(cb.from_user.id):
                await cb.answer("⛔ Нет доступа", show_alert=True)
                return
            cid = cb.message.chat.id
            action = cb.data.split(":", 1)[1]

            if action == "close":
                try:
                    await cb.message.delete()
                except Exception:
                    pass
                await cb.answer("Закрыто")
                return

            if action == "status":
                await self._show_status(cid, cb.message)
                await cb.answer()
                return
            if action == "artifacts":
                await self._show_artifacts(cid, cb.message)
                await cb.answer()
                return
            if action == "push":
                await self._push_current_task(cid, cb.message)
                await cb.answer("Push запущен")
                return

            if action == "cleanup":
                deleted = await self._cleanup_chat_runtime(cid)
                back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]])
                await cb.message.edit_text(f"🧹 Очистка выполнена. Удалено ключей: {deleted}", reply_markup=back)
                await cb.answer("Очищено")
                return

            active = await self.redis.get(f"active_task:{cid}")
            if not active:
                back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]])
                await cb.message.edit_text("📭 Нет активной задачи.", reply_markup=back)
                await cb.answer()
                return

            tid = int(active.decode())
            if action == "stop":
                await self._clear_task_runtime_keys(cid, tid)
                back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]])
                await cb.message.edit_text("🛑 Задача остановлена. Pending-ходы очищены.", reply_markup=back)
                await cb.answer("Остановлено")
                return

            if action == "finalize":
                task_raw = await self.redis.get(f"task_desc:{cid}:{tid}")
                td = task_raw.decode() if task_raw else ""
                await self.redis.setex(f"turn:{cid}:{tid}", 600, "coordinator")
                await self.redis.setex(f"final_reason:{cid}:{tid}", 600, "Пользователь нажал Финализировать сейчас.")
                await self.redis.setex(f"pending:{cid}:coordinator", 300, f"{tid}:{td}")
                back = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Статус", callback_data="task:status")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
                ])
                await cb.message.edit_text("✅ Финализация запущена. Координатор подготовит финальный ответ ближайшим ходом.", reply_markup=back)
                await cb.answer("Финализирую")
                return

            await cb.answer("Неизвестное действие", show_alert=True)

        @self.router.callback_query(F.data.startswith("team:"))
        async def team_cb(cb: CallbackQuery):
            if self.role != "coordinator":
                await cb.answer()
                return
            if cb.from_user and not allowed_user_id(cb.from_user.id):
                await cb.answer("⛔ Нет доступа", show_alert=True)
                return
            cid = cb.message.chat.id
            action = cb.data.split(":", 1)[1]
            if action.startswith("toggle:"):
                role = action.split(":", 1)[1]
                if role not in ROLE_ORDER or role == "coordinator":
                    await cb.answer("Координатор обязателен", show_alert=True)
                    return
                team = await self._get_team(cid)
                if role in team:
                    if len(team) <= 2:
                        await cb.answer("Минимум 2 агента", show_alert=True)
                        return
                    team.remove(role)
                else:
                    team.append(role)
                team = [r for r in ROLE_ORDER if r in set(team)]
                if "coordinator" not in team:
                    team.insert(0, "coordinator")
                await self._set_team(cid, team)
                await self._show_team_picker(cid, cb.message)
                await cb.answer("Команда обновлена")
                return
            presets = {
                "all": ROLE_ORDER,
                "core": ["coordinator", "researcher", "executor", "critic"],
                "tech": ["coordinator", "researcher", "architect", "executor", "qa", "critic"],
                "fast": ["coordinator", "executor", "critic"],
            }
            if action in presets:
                await self._set_team(cid, list(presets[action]))
                await self._show_team_picker(cid, cb.message)
                await cb.answer("Пресет выбран")
                return
            await cb.answer("❌")

        @self.router.callback_query(F.data.startswith("skill:"))
        async def skill_cb(cb: CallbackQuery):
            if self.role != "coordinator":
                await cb.answer()
                return
            if cb.from_user and not allowed_user_id(cb.from_user.id):
                await cb.answer("⛔ Нет доступа", show_alert=True)
                return
            cid = cb.message.chat.id
            action = cb.data.split(":", 1)[1]
            if action.startswith("toggle:"):
                sid = action.split(":", 1)[1]
                skills = await self._get_enabled_skills(cid)
                if sid in skills:
                    skills.remove(sid)
                else:
                    skills.append(sid)
                await self._set_enabled_skills(cid, skills)
                await self._show_skills(cid, cb.message)
                await cb.answer("Skills обновлены")
                return
            if action == "all":
                await self._set_enabled_skills(cid, list(list_skills().keys()))
                await self._show_skills(cid, cb.message)
                await cb.answer("Все skills включены")
                return
            if action == "none":
                await self._set_enabled_skills(cid, [])
                await self._show_skills(cid, cb.message)
                await cb.answer("Skills выключены")
                return
            await cb.answer("❌")

        @self.router.callback_query(F.data.startswith("promptagent:"))
        async def promptagent_cb(cb: CallbackQuery):
            if self.role != "coordinator":
                await cb.answer()
                return
            r = cb.data.split(":", 1)[1]
            if r not in AGENT_BOTS:
                await cb.answer("❌")
                return
            await self._show_prompt_variants(cb.message.chat.id, r, cb.message)
            await cb.answer()

        @self.router.callback_query(F.data.startswith("setprompt:"))
        async def setprompt_cb(cb: CallbackQuery):
            if self.role != "coordinator":
                await cb.answer()
                return
            parts = cb.data.split(":")
            if len(parts) != 3:
                await cb.answer("❌")
                return
            r, key = parts[1], parts[2]
            if r not in PROMPT_VARIANTS or key not in PROMPT_VARIANTS[r]:
                await cb.answer("❌")
                return
            await self.redis.setex(f"prompt_variant:{cb.message.chat.id}:{r}", 86400 * 30, key)
            variant = PROMPT_VARIANTS[r][key]
            back = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к промптам", callback_data=f"promptagent:{r}")],
                [InlineKeyboardButton(text="📝 Все промпты", callback_data="cmd:agentprompts")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
            ])
            await cb.message.edit_text(f"✅ {AGENT_BOTS[r]['emoji']} {AGENT_BOTS[r]['name']}\nПромпт: <b>{variant['name']}</b>", parse_mode="HTML", reply_markup=back)
            await cb.answer("Промпт выбран")

        @self.router.callback_query(F.data.startswith("gm:"))
        async def gm_cb(cb: CallbackQuery):
            k = cb.data.split(":")[1]
            if k not in FREE_MODELS:
                await cb.answer("❌")
                return
            m = FREE_MODELS[k]
            await self.redis.setex(f"global_model:{cb.message.chat.id}", 86400, m["id"])
            back = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к моделям", callback_data="cmd:model")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
            ])
            await cb.message.edit_text(f"✅ {m['name']}\n<code>{m['id']}</code>", parse_mode="HTML", reply_markup=back)
            await cb.answer(m["name"])

        @self.router.callback_query(F.data.startswith("agentcfg:"))
        async def agentcfg_cb(cb: CallbackQuery):
            r = cb.data.split(":", 1)[1]
            if r not in AGENT_BOTS:
                await cb.answer("❌")
                return
            await self._show_agent_card(cb.message.chat.id, r, cb.message)
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
            btns.append([InlineKeyboardButton(text="⬅️ Назад к агенту", callback_data=f"agentcfg:{r}")])
            btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
            await cb.message.edit_text(f"{AGENT_BOTS[r]['emoji']} {AGENT_BOTS[r]['name']} — выбери модель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
            await cb.answer()

        @self.router.callback_query(F.data.startswith("am:"))
        async def am_cb(cb: CallbackQuery):
            p = cb.data.split(":")
            if len(p) != 3 or p[1] not in AGENT_BOTS or p[2] not in FREE_MODELS:
                await cb.answer("❌")
                return
            m = FREE_MODELS[p[2]]
            await self.redis.setex(f"agent_model:{cb.message.chat.id}:{p[1]}", 86400, m["id"])
            back = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к агенту", callback_data=f"agentcfg:{p[1]}")],
                [InlineKeyboardButton(text="🎛 Все агенты", callback_data="cmd:agentmodel")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
            ])
            await cb.message.edit_text(
                f"✅ {AGENT_BOTS[p[1]]['emoji']} {AGENT_BOTS[p[1]]['name']}: {m['name']}\n<code>{m['id']}</code>",
                parse_mode="HTML",
                reply_markup=back,
            )
            await cb.answer()

        @self.router.callback_query(F.data == "resetmodels")
        async def rm_cb(cb: CallbackQuery):
            cid = cb.message.chat.id
            await self.redis.delete(f"global_model:{cid}")
            for r in ROLE_ORDER:
                await self.redis.delete(f"agent_model:{cid}:{r}")
            back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]])
            await cb.message.edit_text("🔄 Сброшено.", reply_markup=back)
            await cb.answer()

        @self.router.callback_query(F.data.startswith("setsteps:"))
        async def ss_cb(cb: CallbackQuery):
            v = int(cb.data.split(":")[1])
            await self.redis.setex(f"max_steps:{cb.message.chat.id}", 86400, str(v))
            back = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к шагам", callback_data="cmd:steps")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
            ])
            await cb.message.edit_text(f"✅ Шагов: {v}", reply_markup=back)
            await cb.answer()

        @self.router.callback_query(F.data.startswith("setdelay:"))
        async def sd_cb(cb: CallbackQuery):
            v = int(cb.data.split(":")[1])
            await self.redis.setex(f"delay:{cb.message.chat.id}", 86400, str(v))
            back = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к задержке", callback_data="cmd:delay")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
            ])
            await cb.message.edit_text(f"✅ Задержка: {v}с", reply_markup=back)
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

    async def _get_prompt_for_role(self, cid, role):
        base = AGENT_BOTS[role]["prompt"]
        raw = await self.redis.get(f"prompt_variant:{cid}:{role}")
        key = raw.decode() if raw else "balanced"
        variant = PROMPT_VARIANTS.get(role, {}).get(key) or PROMPT_VARIANTS.get(role, {}).get("balanced")
        if variant:
            return base + "\n\nДОПОЛНИТЕЛЬНЫЙ СТИЛЬ ПРОМПТА:\n" + variant["suffix"]
        return base

    async def _get_delay(self, cid):
        v = await self.redis.get(f"delay:{cid}")
        return int(v) if v else settings.MIN_REPLY_INTERVAL

    async def _get_max_steps(self, cid):
        v = await self.redis.get(f"max_steps:{cid}")
        return int(v) if v else settings.MAX_DISCUSSION_STEPS

    async def _get_team(self, cid):
        raw = await self.redis.get(f"team:{cid}")
        if raw:
            try:
                team = json.loads(raw.decode())
                team = [r for r in team if r in ROLE_ORDER]
                if team:
                    if "coordinator" not in team:
                        team.insert(0, "coordinator")
                    return [r for r in ROLE_ORDER if r in set(team)]
            except Exception:
                pass
        return list(ROLE_ORDER)

    async def _set_team(self, cid, team):
        team = [r for r in team if r in ROLE_ORDER]
        if "coordinator" not in team:
            team.insert(0, "coordinator")
        team = [r for r in ROLE_ORDER if r in set(team)]
        await self.redis.setex(f"team:{cid}", 86400 * 30, json.dumps(team))

    async def _get_task_team(self, cid, tid):
        raw = await self.redis.get(f"task_team:{cid}:{tid}")
        if raw:
            try:
                team = json.loads(raw.decode())
                return [r for r in ROLE_ORDER if r in set(team)] or list(ROLE_ORDER)
            except Exception:
                pass
        return await self._get_team(cid)

    async def _get_enabled_skills(self, cid):
        raw = await self.redis.get(f"skills_enabled:{cid}")
        all_ids = list(list_skills().keys())
        if raw is None:
            return all_ids
        try:
            return [x for x in json.loads(raw.decode()) if x in all_ids]
        except Exception:
            return all_ids

    async def _set_enabled_skills(self, cid, skills):
        skills = [s for s in skills if s in list_skills()]
        await self.redis.setex(f"skills_enabled:{cid}", 86400 * 30, json.dumps(skills))

    async def _get_task_type(self, cid, tid):
        raw = await self.redis.get(f"task_type:{cid}:{tid}")
        return raw.decode() if raw else getattr(settings, "DEFAULT_TASK_TYPE", "general")

    async def _get_task_budget(self, cid, tid):
        raw = await self.redis.get(f"step_budget:{cid}:{tid}")
        if raw:
            try:
                return json.loads(raw.decode())
            except Exception:
                pass
        return budget_for_task_type(await self._get_task_type(cid, tid))

    async def _has_artifacts(self, cid, tid):
        try:
            artifacts = await load_artifacts(self.redis, cid, tid)
            return bool(artifacts)
        except Exception:
            return False

    def _detect_stagnation(self, history):
        if len(history) < 4:
            return False
        normalized = []
        for item in history[-4:]:
            txt = re.sub(r"\s+", " ", (item.get("content") or "").lower())[:700]
            txt = re.sub(r"[0-9]+", "#", txt)
            normalized.append(txt)
        return len(set(normalized)) <= 2

    async def _task_readiness(self, cid, tid, task_type, team):
        history = await self._get_history(cid, tid)
        text = "\n".join([h.get("content", "") for h in history[-14:]]).lower()
        roles_seen = await self._get_roles_seen(cid, tid)
        has_artifact = await self._has_artifacts(cid, tid)
        if not has_artifact and "[file:" in text:
            # Try to recover from history once; readiness must be based on real saved artifact.
            try:
                await self._recover_artifacts_from_history(cid, tid)
                has_artifact = await self._has_artifacts(cid, tid)
            except Exception:
                has_artifact = False
        has_executor = "executor" in roles_seen or "executor" not in team
        has_qa = "qa" in roles_seen or "qa" not in team
        has_critic = "critic" in roles_seen or "critic" not in team

        if task_type == "simple_artifact":
            return has_artifact and (has_qa or has_critic)
        if task_type == "simple_answer":
            return has_executor or has_critic
        if task_type == "debug":
            has_cause = any(x in text for x in ["причина", "root cause", "ошибка", "лог", "симптом"])
            has_fix = any(x in text for x in ["исправ", "решение", "fix", "сделать", "рекоменд"])
            return has_cause and has_fix and (has_qa or has_critic)
        if task_type == "architecture":
            has_arch = "architect" in roles_seen or "architect" not in team
            has_risks = any(x in text for x in ["риск", "огранич", "компромисс", "безопас"])
            return has_arch and has_executor and (has_qa or has_critic) and has_risks
        if task_type == "research":
            return "researcher" in roles_seen and has_critic
        return has_executor and (has_qa or has_critic)

    def _next_role_after(self, current_role, team):
        team = [r for r in team if r in ROLE_ORDER]
        if not team:
            team = list(ROLE_ORDER)
        if current_role in team:
            idx = team.index(current_role)
            return team[(idx + 1) % len(team)]
        return team[0]

    async def _send_or_edit(self, cid, text, reply_markup=None, parse_mode="HTML", message=None):
        """Редактирует текущее меню по inline-кнопке, а не плодит новые сообщения."""
        if message:
            try:
                await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
                return
            except Exception as e:
                err = str(e).lower()
                if "message is not modified" in err:
                    return
                logger.warning(f"Menu edit failed, send new: {str(e)[:120]}")
        await self.bot.send_message(cid, text, parse_mode=parse_mode, reply_markup=reply_markup)

    async def _back_markup(self, *rows):
        buttons = list(rows)
        buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    def _split_telegram_text(self, text, limit=3900):
        """Разбивает длинный текст на части под лимит Telegram 4096.

        Режем по абзацам/строкам, а если строка слишком длинная — жёстко по limit.
        3900 оставляет запас под префикс "часть X/Y".
        """
        text = str(text or "")
        if len(text) <= limit:
            return [text]

        chunks = []
        current = ""
        paragraphs = text.split("\n")
        for part in paragraphs:
            candidate = part if not current else current + "\n" + part
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            while len(part) > limit:
                cut = part.rfind(" ", 0, limit)
                if cut < limit // 2:
                    cut = limit
                chunks.append(part[:cut].rstrip())
                part = part[cut:].lstrip()
            current = part
        if current:
            chunks.append(current)
        return chunks

    async def _send_long_message(self, cid, text, parse_mode=None, prefix_parts=True):
        """Отправляет длинный текст несколькими Telegram-сообщениями без потери хвоста."""
        chunks = self._split_telegram_text(text)
        total = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            out = chunk
            if prefix_parts and total > 1:
                out = f"📄 Часть {i}/{total}\n\n{chunk}"
            try:
                await self.bot.send_message(cid, out, parse_mode=parse_mode)
            except Exception:
                await self.bot.send_message(cid, re.sub(r'<[^>]+>', '', out))
            if total > 1:
                await asyncio.sleep(0.3)

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
        if is_human and message.from_user and not allowed_user_id(message.from_user.id):
            if self.role == "coordinator":
                await self.bot.send_message(cid, "⛔ Нет доступа.")
            return
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
            if cmd in ("/prompts", "/prompt"):
                await self._show_agent_prompt_picker(cid)
                return
            if cmd == "/team":
                await self._show_team_picker(cid)
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
            if cmd == "/status":
                await self._show_status(cid)
                return
            if cmd in ("/history", "/last"):
                await self._show_history(cid)
                return
            if cmd == "/memory":
                await self._show_memory(cid)
                return
            if cmd.startswith("/remember"):
                text_to_remember = text.replace("/remember", "", 1).strip()
                if not text_to_remember:
                    await self.bot.send_message(cid, "🧠 Использование: <code>/remember важный факт</code>", parse_mode="HTML")
                    return
                try:
                    mem = await remember(cid, text_to_remember, category="manual")
                    await self.bot.send_message(cid, f"✅ Запомнил: <code>{mem['value'][:500]}</code>", parse_mode="HTML")
                except Exception as e:
                    await self.bot.send_message(cid, f"❌ Не смог сохранить память: {str(e)[:120]}")
                return
            if cmd.startswith("/memory_search"):
                q = text.replace("/memory_search", "", 1).strip()
                if not q:
                    await self.bot.send_message(cid, "🔎 Использование: <code>/memory_search запрос</code>", parse_mode="HTML")
                    return
                mems = await search_chat_memories(cid, q)
                await self.bot.send_message(cid, format_memories(mems), parse_mode="HTML")
                return
            if cmd == "/forget":
                await clear_chat_memories(cid)
                await self.bot.send_message(cid, "🗑️ Память очищена.")
                return
            if cmd == "/events":
                await self._show_events(cid)
                return
            if cmd == "/artifacts":
                await self._show_artifacts(cid)
                return
            if cmd == "/github":
                await self._show_github_status(cid)
                return
            if cmd == "/push":
                await self._push_current_task(cid)
                return
            if cmd == "/plan":
                await self._show_plan(cid)
                return
            if cmd == "/skills":
                await self._show_skills(cid)
                return
            if cmd == "/context":
                await self._show_context(cid)
                return
            if cmd == "/cleanup":
                deleted = await self._cleanup_chat_runtime(cid)
                await self.bot.send_message(cid, f"🧹 Очистка выполнена. Удалено ключей: {deleted}")
                return
            if cmd == "/finalize":
                active = await self.redis.get(f"active_task:{cid}")
                if not active:
                    await self.bot.send_message(cid, "📭 Нет активной задачи.")
                    return
                tid = int(active.decode())
                task_raw = await self.redis.get(f"task_desc:{cid}:{tid}")
                td = task_raw.decode() if task_raw else ""
                await self.redis.setex(f"turn:{cid}:{tid}", 600, "coordinator")
                await self.redis.setex(f"final_reason:{cid}:{tid}", 600, "Пользователь вызвал /finalize.")
                await self.redis.setex(f"pending:{cid}:coordinator", 300, f"{tid}:{td}")
                await self.bot.send_message(cid, "✅ Финализация запущена.")
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

    async def _cleanup_chat_runtime(self, cid):
        """Грубая очистка runtime-ключей чата: active/pending/turn/locks/rate."""
        patterns = [
            f"active_task:{cid}",
            f"pending:{cid}:*",
            f"turn:{cid}:*",
            f"final_reason:{cid}:*",
            f"llm_fail:{cid}:*",
            f"finalizing:{cid}:*",
            f"lock:task:{cid}:*",
            f"rate:*:{cid}",
        ]
        keys = []
        for pattern in patterns:
            if "*" in pattern:
                async for key in self.redis.scan_iter(pattern):
                    keys.append(key)
            else:
                keys.append(pattern)
        if keys:
            return await self.redis.delete(*keys)
        return 0

    async def _acquire_task_lock(self, cid, tid, ttl=180):
        return await self.redis.set(f"lock:task:{cid}:{tid}", self.role, nx=True, ex=ttl)

    async def _release_task_lock(self, cid, tid):
        k = f"lock:task:{cid}:{tid}"
        v = await self.redis.get(k)
        if v and v.decode() == self.role:
            await self.redis.delete(k)

    async def _get_roles_seen(self, cid, tid):
        """Какие роли реально участвовали как авторы сообщений, а не просто были упомянуты."""
        history = await self._get_history(cid, tid)
        seen = set()
        sender_to_role = {}
        for role in ROLE_ORDER:
            cfg = AGENT_BOTS.get(role, {})
            sender_to_role[role.lower()] = role
            sender_to_role[str(cfg.get("name", "")).lower()] = role
        sender_to_role.update({
            "координатор": "coordinator",
            "исследователь": "researcher",
            "архитектор": "architect",
            "исполнитель": "executor",
            "qa": "qa",
            "тестировщик": "qa",
            "критик": "critic",
        })

        for item in history:
            content = (item.get("content") or "").strip()
            sender = content.split(":", 1)[0].strip().lower() if ":" in content else ""
            sender_clean = re.sub(r"[^a-zа-яё]+", "", sender, flags=re.IGNORECASE)
            for key, role in sender_to_role.items():
                key_clean = re.sub(r"[^a-zа-яё]+", "", key, flags=re.IGNORECASE)
                if key_clean and key_clean == sender_clean:
                    seen.add(role)
                    break
        return seen

    async def _get_active_tid(self, cid):
        active = await self.redis.get(f"active_task:{cid}")
        return int(active.decode()) if active else None

    async def _show_plan(self, cid, message=None):
        tid = await self._get_active_tid(cid)
        if not tid:
            text = "🧭 <b>План</b>\n\n📭 Нет активной задачи."
        else:
            plan = await get_run_plan(self.redis, cid, tid)
            text = f"🧭 <b>План задачи #{tid}</b>\n\n" + format_plan(plan)
        btns = [[InlineKeyboardButton(text="🔄 Обновить", callback_data="cmd:plan")], [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_events(self, cid, message=None):
        tid = await self._get_active_tid(cid)
        if not tid:
            text = "📋 <b>Events</b>\n\n📭 Нет активной задачи."
        else:
            events = await get_run_events(self.redis, cid, tid, limit=40)
            text = f"📋 <b>Events задачи #{tid}</b>\n\n" + format_events(events)
            if len(text) > 3900:
                text = text[-3900:]
        btns = [[InlineKeyboardButton(text="🔄 Обновить", callback_data="cmd:events")], [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_memory(self, cid, message=None):
        mems = await list_chat_memories(cid, limit=20)
        text = format_memories(mems)
        text += "\n\n<code>/remember факт</code> — запомнить\n<code>/memory_search запрос</code> — поиск\n<code>/forget</code> — очистить"
        btns = [
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="cmd:memory")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
        ]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_history(self, cid, message=None):
        """Показывает последние задачи из Postgres."""
        try:
            from sqlalchemy import select
            from app.db.session import get_session
            from app.db.models import Task as DBTask
            async with get_session() as session:
                res = await session.execute(
                    select(DBTask).where(DBTask.chat_id == cid).order_by(DBTask.created_at.desc()).limit(8)
                )
                tasks = list(res.scalars().all())
        except Exception as e:
            logger.warning(f"History load failed: {str(e)[:120]}")
            tasks = []

        if not tasks:
            text = "📜 <b>История пуста</b>\n\nНовые multi-bot задачи будут сохраняться после этого обновления."
            btns = [[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]
            await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)
            return

        text = "📜 <b>Последние задачи</b>\n\n"
        btns = []
        for t in tasks:
            status = getattr(t.status, "value", str(t.status))
            desc = (t.description or "")[:70].replace("<", "&lt;").replace(">", "&gt;")
            text += f"<b>#{t.id}</b> · <code>{status}</code> · {t.current_step}/{t.max_steps}\n<i>{desc}</i>\n\n"
            btns.append([InlineKeyboardButton(text=f"#{t.id} · {desc[:28]}", callback_data=f"hist:{t.id}")])
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu"), InlineKeyboardButton(text="❌ Закрыть", callback_data="task:close")])
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_task_detail(self, cid, db_task_id, message=None):
        """Показывает детали одной задачи из Postgres."""
        try:
            from sqlalchemy import select
            from app.db.session import get_session
            from app.db.models import Task as DBTask, Message as DBMessage
            async with get_session() as session:
                task_res = await session.execute(select(DBTask).where(DBTask.id == db_task_id, DBTask.chat_id == cid))
                task = task_res.scalar_one_or_none()
                msg_res = await session.execute(
                    select(DBMessage).where(DBMessage.task_id == db_task_id).order_by(DBMessage.created_at.desc()).limit(6)
                )
                messages = list(msg_res.scalars().all())
        except Exception as e:
            logger.warning(f"Task detail load failed: {str(e)[:120]}")
            task, messages = None, []

        if not task:
            text = "❌ Задача не найдена."
        else:
            status = getattr(task.status, "value", str(task.status))
            desc = (task.description or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            final = (task.final_answer or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            text = (
                f"📄 <b>Задача #{task.id}</b>\n\n"
                f"<b>Статус:</b> <code>{status}</code>\n"
                f"<b>Шаги:</b> {task.current_step}/{task.max_steps}\n"
                f"<b>Модель:</b> <code>{task.model or ''}</code>\n\n"
                f"<b>Описание:</b>\n<i>{desc[:900]}</i>\n\n"
            )
            if final:
                text += f"<b>Финал:</b>\n<code>{final[:900]}</code>\n\n"
            if messages:
                text += "<b>Последние сообщения:</b>\n"
                for m in reversed(messages):
                    clean = re.sub(r"<[^>]+>", "", m.content or "")
                    clean = clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    text += f"• <b>{m.role}</b>: <code>{clean[:220]}</code>\n"
            if len(text) > 3900:
                text = text[:3900] + "..."
        btns = [
            [InlineKeyboardButton(text="⬅️ История", callback_data="cmd:history")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu"), InlineKeyboardButton(text="❌ Закрыть", callback_data="task:close")],
        ]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_status(self, cid, message=None):
        active = await self.redis.get(f"active_task:{cid}")
        if not active:
            text = "📭 <b>Нет активной задачи</b>\n\nМожно начать новую: <code>Задача: описание</code>"
            btns = [
                [InlineKeyboardButton(text="🧹 Cleanup", callback_data="task:cleanup")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu"), InlineKeyboardButton(text="❌ Закрыть", callback_data="task:close")],
            ]
            await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)
            return

        tid = int(active.decode())
        task_raw = await self.redis.get(f"task_desc:{cid}:{tid}")
        td = task_raw.decode() if task_raw else ""
        sr = await self.redis.get(f"steps:{cid}:{tid}")
        steps = int(sr) if sr else 0
        task_type = await self._get_task_type(cid, tid)
        budget = await self._get_task_budget(cid, tid)
        ms = int(budget.get("hard", await self._get_max_steps(cid)))
        ready = await self._task_readiness(cid, tid, task_type, await self._get_task_team(cid, tid))
        turn = await self.redis.get(f"turn:{cid}:{tid}")
        turn_s = turn.decode() if turn else "не задан"
        lock = await self.redis.get(f"lock:task:{cid}:{tid}")
        lock_s = lock.decode() if lock else "нет"
        pending = []
        for role in ROLE_ORDER:
            if await self.redis.get(f"pending:{cid}:{role}"):
                pending.append(role)
        final_reason = await self.redis.get(f"final_reason:{cid}:{tid}")
        hist = await self._get_history(cid, tid)
        last = hist[-1]["content"][:350] if hist else "нет сообщений"
        last_clean = re.sub(r"<[^>]+>", "", last)
        td_safe = td[:600].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        last_safe = last_clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        models = []
        for r in ROLE_ORDER:
            model_short = (await self._get_model_for_role(cid, r)).split("/")[-1].replace(":free", "")[:28]
            models.append(f"{AGENT_BOTS[r]['emoji']} {AGENT_BOTS[r]['name']}: <code>{model_short}</code>")

        text = (
            f"📊 <b>Статус задачи #{tid}</b>\n\n"
            f"<b>Шаги:</b> {steps}/{ms}\n"
            f"<b>Текущий ход:</b> <code>{turn_s}</code>\n"
            f"<b>Lock:</b> <code>{lock_s}</code>\n"
            f"<b>Pending:</b> <code>{', '.join(pending) if pending else 'нет'}</code>\n"
            f"<b>Финализация:</b> {'да' if final_reason else 'нет'}\n\n"
            f"<b>Задача:</b>\n<i>{td_safe}</i>\n\n"
            f"<b>Модели:</b>\n" + "\n".join(models) + "\n\n"
            f"<b>Последнее сообщение:</b>\n<code>{last_safe}</code>"
        )
        if len(text) > 3900:
            text = text[:3900] + "..."
        btns = [
            [InlineKeyboardButton(text="✅ Финализировать сейчас", callback_data="task:finalize")],
            [InlineKeyboardButton(text="🛑 Остановить", callback_data="task:stop"), InlineKeyboardButton(text="🧹 Cleanup", callback_data="task:cleanup")],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="task:status"), InlineKeyboardButton(text="📜 История", callback_data="cmd:history")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu"), InlineKeyboardButton(text="❌ Закрыть", callback_data="task:close")],
        ]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_menu(self, cid, message=None):
        gmr = await self.redis.get(f"global_model:{cid}")
        gm = (gmr.decode() if gmr else settings.DEFAULT_MODEL).split("/")[-1].replace(":free", "")
        ms = await self._get_max_steps(cid)
        dl = await self._get_delay(cid)
        active = await self.redis.get(f"active_task:{cid}")
        status = "🟢 активна" if active else "⚪ нет активной задачи"

        btns = [
            [InlineKeyboardButton(text="👥 Команда", callback_data="cmd:team"), InlineKeyboardButton(text="🎛 Модели агентов", callback_data="cmd:agentmodel")],
            [InlineKeyboardButton(text="📝 Промпты", callback_data="cmd:agentprompts"), InlineKeyboardButton(text="🤖 Все агенты", callback_data="cmd:agents")],
            [InlineKeyboardButton(text="🤖 Общая модель", callback_data="cmd:model"), InlineKeyboardButton(text="📋 Все модели", callback_data="cmd:models")],
            [InlineKeyboardButton(text="📊 Статус", callback_data="cmd:status"), InlineKeyboardButton(text="📜 История", callback_data="cmd:history")],
            [InlineKeyboardButton(text="🧠 Память", callback_data="cmd:memory"), InlineKeyboardButton(text="🧩 Skills", callback_data="cmd:skills")],
            [InlineKeyboardButton(text="🧭 План", callback_data="cmd:plan"), InlineKeyboardButton(text="📋 Events", callback_data="cmd:events")],
            [InlineKeyboardButton(text="📦 Артефакты", callback_data="cmd:artifacts"), InlineKeyboardButton(text="🚀 GitHub", callback_data="cmd:github")],
            [InlineKeyboardButton(text="✅ Финализировать", callback_data="task:finalize"), InlineKeyboardButton(text="🧹 Cleanup", callback_data="task:cleanup")],
            [InlineKeyboardButton(text="📊 Шаги", callback_data="cmd:steps"), InlineKeyboardButton(text="⏱ Задержка", callback_data="cmd:delay")],
            [InlineKeyboardButton(text="📄 Context", callback_data="cmd:context"), InlineKeyboardButton(text="⚙️ Конфиг", callback_data="cmd:config")],
            [InlineKeyboardButton(text="🧩 Free API провайдеры", callback_data="cmd:providers")],
            [InlineKeyboardButton(text="❓ Как пользоваться", callback_data="cmd:help"), InlineKeyboardButton(text="🔄 Сброс моделей", callback_data="resetmodels")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="task:close")],
        ]

        team = await self._get_team(cid)
        team_line = " → ".join([f"{AGENT_BOTS[r]['emoji']} {AGENT_BOTS[r]['name']}" for r in team])
        text = (
            "<b>🚀 AI Agents Team</b>\n"
            "<i>6 Telegram-агентов: координатор, исследователь, архитектор, исполнитель, QA, критик.</i>\n\n"
            f"<b>Состояние:</b> {status}\n"
            f"<b>Активная команда:</b> {team_line}\n"
            f"<b>Общая модель:</b> <code>{gm}</code>\n"
            f"<b>Лимит:</b> {ms} шагов · <b>Пауза:</b> {dl}с\n\n"
            "<b>Быстрый старт:</b>\n"
            "• <code>Задача: опиши задачу</code> — начать обсуждение\n"
            "• <code>/team</code> — выбрать состав агентов\n"
            "• <code>/stop</code> — остановить активную задачу\n"
            "• Замечание агенту: reply на сообщение бота или <code>@Qabotai_bot текст</code>\n\n"
            "<b>Правильные usernames:</b>\n"
            "🎯 <code>@coordintor_ai_bot</code>\n"
            "🔍 <code>@Researcher1_ai_bot</code>\n"
            "🏗️ <code>@Architect1_ai_bot</code>\n"
            "⚡ <code>@executorai_ai_bot</code>\n"
            "🧪 <code>@Qabotai_bot</code>\n"
            "🧐 <code>@criticaibot_bot</code>"
        )
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_team_picker(self, cid, message=None):
        team = await self._get_team(cid)
        team_set = set(team)
        lines = ["👥 <b>Команда ИИ-агентов для этого чата</b>", ""]
        lines.append("<b>Сейчас:</b> " + " → ".join([f"{AGENT_BOTS[r]['emoji']} {AGENT_BOTS[r]['name']}" for r in team]))
        lines.append("")
        lines.append("Выключи ненужных агентов для простых задач. Координатор обязателен.")
        btns = []
        for r in ROLE_ORDER:
            cfg = AGENT_BOTS[r]
            mark = "✅" if r in team_set else "☐"
            lock = " 🔒" if r == "coordinator" else ""
            btns.append([InlineKeyboardButton(text=f"{mark} {cfg['emoji']} {cfg['name']}{lock}", callback_data=f"team:toggle:{r}")])
        btns += [
            [InlineKeyboardButton(text="👑 Все 6", callback_data="team:all"), InlineKeyboardButton(text="🧩 База 4", callback_data="team:core")],
            [InlineKeyboardButton(text="🛠 Тех-команда", callback_data="team:tech"), InlineKeyboardButton(text="⚡ Быстро", callback_data="team:fast")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
        ]
        await self._send_or_edit(cid, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_agents_dashboard(self, cid, message=None):
        btns = []
        for r in ROLE_ORDER:
            cfg = AGENT_BOTS[r]
            model = await self._get_model_for_role(cid, r)
            short = model.split("/")[-1].replace(":free", "")[:22]
            btns.append([InlineKeyboardButton(text=f"{cfg['emoji']} {cfg['name']} · {short}", callback_data=f"agentcfg:{r}")])
        btns.append([InlineKeyboardButton(text="🎛 Быстрая смена моделей", callback_data="cmd:agentmodel")])
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
        await self._send_or_edit(
            cid,
            "👥 <b>Настройки агентов</b>\n\nВыбери агента, чтобы посмотреть роль, username и сменить модель:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
            message=message,
        )

    async def _show_agent_card(self, cid, role, message=None):
        cfg = AGENT_BOTS[role]
        model = await self._get_model_for_role(cid, role)
        username = {
            "coordinator": "@coordintor_ai_bot",
            "researcher": "@Researcher1_ai_bot",
            "architect": "@Architect1_ai_bot",
            "executor": "@executorai_ai_bot",
            "qa": "@Qabotai_bot",
            "critic": "@criticaibot_bot",
        }.get(role, "")
        desc = {
            "coordinator": "управляет ходом обсуждения и финализирует ответ",
            "researcher": "ищет факты, делает краткий анализ, учитывает ваши поправки",
            "architect": "проектирует архитектуру, компоненты, API, данные и инфраструктуру",
            "executor": "делает практический результат: код, текст, план, расчёты",
            "qa": "проверяет результат, тест-кейсы, edge cases и критерии приёмки",
            "critic": "проверяет логику, риски и слабые места",
        }.get(role, "")
        btns = [
            [InlineKeyboardButton(text="🤖 Сменить модель", callback_data=f"pickagent:{role}"), InlineKeyboardButton(text="📝 Промпт", callback_data=f"promptagent:{role}")],
            [InlineKeyboardButton(text="⬅️ Назад к агентам", callback_data="cmd:agents"), InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
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
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_help(self, cid, message=None):
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
        btns = [
            [InlineKeyboardButton(text="👥 Агенты", callback_data="cmd:agents"), InlineKeyboardButton(text="⚙️ Конфиг", callback_data="cmd:config")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
        ]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _get_current_or_last_tid(self, cid):
        active = await self.redis.get(f"active_task:{cid}")
        if active:
            return int(active.decode()), True
        last = await self.redis.get(f"last_task_tid:{cid}")
        if last:
            return int(last.decode()), False
        return None, False

    async def _recover_artifacts_from_history(self, cid, tid):
        recovered = []
        history = await self._get_history(cid, tid)
        for item in history:
            content = item.get("content") or ""
            role = "unknown"
            if ":" in content:
                sender = content.split(":", 1)[0].lower()
                for r, cfg in AGENT_BOTS.items():
                    if r in sender or str(cfg.get("name", "")).lower() in sender:
                        role = r
                        break
            recovered.extend(extract_artifacts_from_text(content, role=role))
        if recovered:
            await save_artifacts(self.redis, cid, tid, recovered)
            await add_run_event(self.redis, cid, tid, "artifacts_recovered", role="system", data={"count": len(recovered), "files": [a.path for a in recovered]})
        return recovered

    async def _show_artifacts(self, cid, message=None):
        tid, is_active = await self._get_current_or_last_tid(cid)
        if not tid:
            text = "📦 <b>Артефакты</b>\n\n📭 Нет активной или последней задачи."
            btns = [[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]
        else:
            artifacts = await load_artifacts(self.redis, cid, tid)
            if not artifacts:
                await self._recover_artifacts_from_history(cid, tid)
                artifacts = await load_artifacts(self.redis, cid, tid)
            status = "активной" if is_active else "последней завершённой"
            text = f"📦 <b>Артефакты {status} задачи #{tid}</b>\n\n" + format_artifacts(artifacts)
            btns = [
                [InlineKeyboardButton(text="🚀 Push в GitHub", callback_data="task:push")],
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="task:artifacts")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
            ]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_github_status(self, cid, message=None):
        repo = settings.GITHUB_REPO or "not_set"
        branch = settings.GITHUB_BRANCH or "main"
        token = "set" if settings.GITHUB_TOKEN else "not_set"
        text = (
            "🚀 <b>GitHub Publisher</b>\n\n"
            f"Repo: <code>{repo}</code>\n"
            f"Base branch: <code>{branch}</code>\n"
            f"Mode: <code>{settings.GITHUB_BRANCH_MODE}</code>\n"
            f"Auto push: <code>{settings.GITHUB_AUTO_PUSH}</code>\n"
            f"Create PR: <code>{settings.GITHUB_CREATE_PR}</code>\n"
            f"Token: <code>{token}</code>\n\n"
            "Команды: <code>/artifacts</code>, <code>/push</code>"
        )
        btns = [[InlineKeyboardButton(text="📦 Артефакты", callback_data="cmd:artifacts")], [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _push_current_task(self, cid, message=None):
        tid, is_active = await self._get_current_or_last_tid(cid)
        if not tid:
            text = "📭 Нет активной или последней задачи для push."
            if message:
                await self._send_or_edit(cid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]), message=message)
            else:
                await self.bot.send_message(cid, text)
            return
        try:
            artifacts = await load_artifacts(self.redis, cid, tid)
            if not artifacts:
                await self._recover_artifacts_from_history(cid, tid)
            dbid = await self.redis.get(f"db_task_id:{cid}:{tid}")
            db_task_id = int(dbid.decode()) if dbid else None
            result = await publish_task_artifacts(self.redis, cid, tid, db_task_id=db_task_id, role="Executor")
            text = f"✅ GitHub push выполнен.\nBranch: <code>{result['branch']}</code>\nCommit: {result['commit_url']}"
            if result.get("pr_url"):
                text += f"\nPR: {result['pr_url']}"
            await add_run_event(self.redis, cid, tid, "github_push", role="executor", data={"branch": result["branch"], "files": result["files"]})
        except GitHubConflictError as e:
            text = f"⚠️ GitHub conflict: <code>{str(e)[:500]}</code>\nНичего не затираю."
        except GitHubPublisherError as e:
            text = f"⚠️ GitHub push failed: <code>{str(e)[:500]}</code>"
        except Exception as e:
            text = f"❌ Unexpected GitHub push error: <code>{str(e)[:500]}</code>"
        if message:
            await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📦 Артефакты", callback_data="cmd:artifacts"), InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]), message=message)
        else:
            await self.bot.send_message(cid, text, parse_mode="HTML")

    async def _show_skills(self, cid, message=None):
        enabled = set(await self._get_enabled_skills(cid))
        registry = list_skills()
        lines = ["🧩 <b>Skills</b>", "", "Skills автоматически подмешиваются в prompt по тексту задачи."]
        btns = []
        for sid, meta in registry.items():
            mark = "✅" if sid in enabled else "☐"
            btns.append([InlineKeyboardButton(text=f"{mark} {meta['name']}", callback_data=f"skill:toggle:{sid}")])
        btns += [
            [InlineKeyboardButton(text="✅ Включить все", callback_data="skill:all"), InlineKeyboardButton(text="☐ Выключить все", callback_data="skill:none")],
            [InlineKeyboardButton(text="📄 Контекст", callback_data="cmd:context"), InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")],
        ]
        await self._send_or_edit(cid, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_context(self, cid, message=None):
        ctx = read_context_files()
        text = "📄 <b>Context Files</b>\n\n"
        if ctx:
            clean = re.sub(r"<[^>]+>", "", ctx)
            clean = clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            text += f"<code>{clean[:3200]}</code>"
        else:
            text += "Контекстные файлы не найдены."
        btns = [[InlineKeyboardButton(text="🧩 Skills", callback_data="cmd:skills")], [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_providers_help(self, cid, message=None):
        st = get_llm_router_status()
        lines = [
            "🧩 <b>LLM Provider Router</b>",
            "",
            "Порядок fallback: выбранная модель → Mistral → OpenRouter/HF/Groq/Cerebras, если есть ключи.",
            "",
            f"<b>Cache:</b> {st['cache_size']} items, TTL {st['cache_ttl_seconds']}s",
            "",
            "<b>Провайдеры:</b>",
        ]
        for name, info in st["providers"].items():
            icon = "🟢" if info["configured"] and info["blocked_seconds"] == 0 else ("🟡" if info["configured"] else "⚪")
            block = f" blocked {info['blocked_seconds']}s" if info["blocked_seconds"] else ""
            stats = info.get("stats", {})
            lines.append(
                f"{icon} <b>{name}</b>: key=<code>{info['key']}</code>{block} "
                f"ok={stats.get('success',0)} fail={stats.get('fail',0)} skip={stats.get('skip',0)}"
            )
            if info.get("last_error"):
                lines.append(f"   <code>{str(info['last_error'])[:120]}</code>")
        lines += [
            "",
            "<b>Команды:</b>",
            "• <code>/status</code> — задача и lock",
            "• <code>/cleanup</code> — очистить Redis runtime",
            "• <code>/finalize</code> — финальный ответ сейчас",
        ]
        text = "\n".join(lines)
        await self._send_or_edit(
            cid,
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="cmd:providers")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu"), InlineKeyboardButton(text="❌ Закрыть", callback_data="task:close")],
            ]),
            message=message,
        )

    async def _show_agent_prompt_picker(self, cid, message=None):
        btns = []
        for r in ROLE_ORDER:
            cfg = AGENT_BOTS[r]
            raw = await self.redis.get(f"prompt_variant:{cid}:{r}")
            key = raw.decode() if raw else "balanced"
            vname = PROMPT_VARIANTS.get(r, {}).get(key, {}).get("name", key)
            btns.append([InlineKeyboardButton(text=f"{cfg['emoji']} {cfg['name']}: {vname}", callback_data=f"promptagent:{r}")])
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
        await self._send_or_edit(cid, "📝 <b>Промпты агентов</b>\n\nВыбери агента и стиль поведения:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_prompt_variants(self, cid, role, message=None):
        cfg = AGENT_BOTS[role]
        raw = await self.redis.get(f"prompt_variant:{cid}:{role}")
        cur = raw.decode() if raw else "balanced"
        btns = []
        for key, variant in PROMPT_VARIANTS.get(role, {}).items():
            mark = "✅ " if key == cur else ""
            btns.append([InlineKeyboardButton(text=f"{mark}{variant['name']}", callback_data=f"setprompt:{role}:{key}")])
        btns.append([InlineKeyboardButton(text="⬅️ Все промпты", callback_data="cmd:agentprompts")])
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
        text = f"{cfg['emoji']} <b>{cfg['name']}</b> — стиль промпта\n\nТекущий: <code>{cur}</code>"
        await self._send_or_edit(cid, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_model_picker(self, cid, message=None):
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
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
        await self._send_or_edit(cid, "🤖 <b>Общая модель:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_agent_model_picker(self, cid, message=None):
        gmr = await self.redis.get(f"global_model:{cid}")
        df = gmr.decode() if gmr else settings.DEFAULT_MODEL
        btns = []
        for r in ROLE_ORDER:
            am = await self.redis.get(f"agent_model:{cid}:{r}")
            cur = (am.decode() if am else df).split("/")[-1].replace(":free", "")[:18]
            btns.append([InlineKeyboardButton(text=f"{AGENT_BOTS[r]['emoji']} {AGENT_BOTS[r]['name']}: {cur}", callback_data=f"pickagent:{r}")])
        btns.append([InlineKeyboardButton(text="🔄 Сброс", callback_data="resetmodels")])
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
        await self._send_or_edit(cid, "🎛 <b>Модели агентов:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_models_list(self, cid, message=None):
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
        await self._send_or_edit(
            cid, t, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]),
            message=message,
        )

    async def _show_config(self, cid, message=None):
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
        await self._send_or_edit(
            cid, t, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")]]),
            message=message,
        )

    async def _show_steps_picker(self, cid, message=None):
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
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
        await self._send_or_edit(cid, f"📊 <b>Шагов ({cur}):</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

    async def _show_delay_picker(self, cid, message=None):
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
        btns.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="cmd:menu")])
        await self._send_or_edit(cid, f"⏱ <b>Задержка ({cur}с):</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), message=message)

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
        await self.redis.setex(f"last_task_tid:{cid}", 86400 * 7, str(tid))
        await self.redis.setex(f"task_desc:{cid}:{tid}", 7200, task)
        task_type = classify_task(task) if getattr(settings, "DYNAMIC_STEPS_ENABLED", True) else "manual"
        budget = budget_for_task_type(task_type)
        team = await self._get_team(cid)
        if getattr(settings, "AUTO_TEAM_BY_TASK_TYPE", True) and task_type in TASK_TEAMS:
            team = [r for r in TASK_TEAMS[task_type] if r in ROLE_ORDER]
        await self.redis.setex(f"task_type:{cid}:{tid}", 7200, task_type)
        await self.redis.setex(f"step_budget:{cid}:{tid}", 7200, json.dumps(budget))
        await self.redis.setex(f"task_team:{cid}:{tid}", 7200, json.dumps(team))
        plan = create_plan_for_team(task, team)
        await save_run_plan(self.redis, cid, tid, plan)
        enabled_skills = await self._get_enabled_skills(cid)
        task_skills = select_skills_for_task(task, enabled=enabled_skills)
        await self.redis.setex(f"task_skills:{cid}:{tid}", 7200, json.dumps(task_skills))
        await add_run_event(self.redis, cid, tid, "task_started", role="coordinator", data={"team": team, "task": task[:200], "skills": task_skills, "task_type": task_type, "budget": budget})
        await self.redis.set(f"steps:{cid}:{tid}", "0")
        await self.redis.expire(f"steps:{cid}:{tid}", 7200)
        await self.redis.setex(f"turn:{cid}:{tid}", 600, "coordinator")
        model = await self._get_model(cid)
        ms = await self._get_max_steps(cid)
        dl = await self._get_delay(cid)
        try:
            db_task = await create_task(cid, message.from_user.id if message.from_user else 0, task, model)
            await update_task(db_task.id, max_steps=ms)
            await self.redis.setex(f"db_task_id:{cid}:{tid}", 7200, str(db_task.id))
        except Exception as e:
            logger.warning(f"DB task create failed: {str(e)[:120]}")
        team_label = " → ".join([AGENT_BOTS[r]["emoji"] for r in team])
        await self.bot.send_message(cid, f"🎯 Задача!\n\n📝 {task}\n🤖 {model.split('/')[-1]}\n👥 {team_label}\n📊 {ms} шагов ⏱ {dl}с\n\nНачинаю...")
        await asyncio.sleep(2)
        await self._think_and_reply(cid, tid, task, [], 0)

    async def _clear_task_runtime_keys(self, cid, tid):
        """Удаляет все runtime-ключи задачи, чтобы старые pending не крутили завершённую задачу."""
        await self.redis.delete(f"active_task:{cid}")
        await self.redis.delete(f"turn:{cid}:{tid}")
        await self.redis.delete(f"final_reason:{cid}:{tid}")
        await self.redis.delete(f"llm_fail:{cid}:{tid}")
        await self.redis.delete(f"finalizing:{cid}:{tid}")
        await self.redis.delete(f"lock:task:{cid}:{tid}")
        await self.redis.delete(f"db_task_id:{cid}:{tid}")
        await self.redis.delete(f"task_team:{cid}:{tid}")
        await self.redis.delete(f"task_type:{cid}:{tid}")
        await self.redis.delete(f"step_budget:{cid}:{tid}")
        await self.redis.delete(f"task_skills:{cid}:{tid}")
        for role in ROLE_ORDER:
            await self.redis.delete(f"pending:{cid}:{role}")
            await self.redis.delete(f"rate:{role}:{cid}")

    async def _clear_stale_task_keys(self, cid, tid):
        """Удаляет ключи старой задачи, не трогая active_task и pending новой задачи."""
        await self.redis.delete(
            f"turn:{cid}:{tid}",
            f"final_reason:{cid}:{tid}",
            f"llm_fail:{cid}:{tid}",
            f"finalizing:{cid}:{tid}",
            f"lock:task:{cid}:{tid}",
            f"task_team:{cid}:{tid}",
            f"task_type:{cid}:{tid}",
            f"step_budget:{cid}:{tid}",
            f"task_skills:{cid}:{tid}",
        )

    async def _complete_task(self, cid, tid, completion_message="✅ Задача завершена. Новые ходы агентов остановлены.", final_answer=None):
        """Единая точка завершения задачи."""
        try:
            dbid = await self.redis.get(f"db_task_id:{cid}:{tid}")
            if dbid:
                await update_task_status(int(dbid.decode()), TaskStatus.COMPLETED, final_answer or completion_message)
        except Exception as e:
            logger.warning(f"DB task complete failed: {str(e)[:120]}")
        try:
            dbid = await self.redis.get(f"db_task_id:{cid}:{tid}")
            lesson_task_id = int(dbid.decode()) if dbid else tid
            await save_task_lesson(cid, lesson_task_id, final_answer)
        except Exception as e:
            logger.warning(f"Save task lesson failed: {str(e)[:120]}")
        await add_run_event(self.redis, cid, tid, "task_completed", role=self.role, data={"message": completion_message[:200] if completion_message else ""})
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
            task_type = await self._get_task_type(cid, tid)
            if self._artifact_required_for_task(td, task_type) and not await self._has_artifacts(cid, tid):
                await self._recover_artifacts_from_history(cid, tid)
                if not await self._has_artifacts(cid, tid):
                    await self._handle_missing_required_artifact(cid, tid, td, "force finalize blocked: artifact required but missing")
                    return

            history = await self._get_history(cid, tid)
            llm_msgs = [
                {"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]}
                for m in history[-12:]
            ]
            prompt = AGENT_BOTS["coordinator"]["prompt"] + CORRECT_USERNAMES_PROMPT + FINALIZATION_PROMPT + STRUCTURED_OUTPUT_PROMPT
            model = await self._get_model_for_role(cid, "coordinator")
            response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, td, model, FALLBACK_MODELS)

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

            response, _next_agent, _final, _parsed_ok = parse_structured_agent_response(response, current_role="coordinator")
            response = normalize_agent_mentions(response)
            if not is_final_response(response):
                response = "[ФИНАЛЬНЫЙ ОТВЕТ]\n" + response

            msg = f"🎯 {response}"
            await self._send_long_message(cid, msg)
            await self._save_message(cid, tid, "Координатор", msg)
            await self._complete_task(cid, tid, "✅ Завершено. Задача закрыта, дальнейшие ходы остановлены.", response)
        except Exception as e:
            logger.error(f"Force finalize error: {e}")
            await self._clear_task_runtime_keys(cid, tid)
            try:
                await self.bot.send_message(cid, f"✅ Обсуждение остановлено по лимиту/ошибке. Причина: {str(e)[:120]}")
            except Exception:
                pass

    def _artifact_required_for_task(self, task_text, task_type):
        t = (task_text or "").lower()
        return task_type == "simple_artifact" or any(
            x in t for x in ["[file:", "github", "push", "артефакт", "docs/", ".md", ".html", "generated_code"]
        )

    async def _handle_missing_required_artifact(self, cid, tid, td, reason="Нужен файл-артефакт, но /artifacts пуст."):
        """Не закрывает файловую задачу без artifacts; возвращает ход Executor-у."""
        retry_key = f"artifact_retry:{cid}:{tid}"
        retries = await self.redis.incr(retry_key)
        await self.redis.expire(retry_key, 7200)

        if retries > 4:
            await add_run_event(self.redis, cid, tid, "artifact_missing_failed", role=self.role, data={"retries": retries})
            await self.bot.send_message(
                cid,
                "❌ Не удалось получить корректный [FILE] artifact после нескольких попыток. "
                "Задача остановлена без GitHub push. Запусти новую задачу с явным форматом файла."
            )
            await self._complete_task(cid, tid, "❌ Задача остановлена: не удалось получить файл-артефакт.", "Artifact missing")
            return

        budget = await self._get_task_budget(cid, tid)
        hard = int(budget.get("hard", await self._get_max_steps(cid)))
        await self.redis.setex(f"steps:{cid}:{tid}", 7200, str(max(0, hard - 2)))
        await self.redis.setex(f"turn:{cid}:{tid}", 600, "executor")
        await self.redis.setex(f"pending:{cid}:executor", 300, f"{tid}:{td}")
        await add_run_event(self.redis, cid, tid, "artifact_missing_retry", role=self.role, data={"retries": retries, "reason": reason})
        await self.bot.send_message(
            cid,
            "⚠️ Нельзя завершить или пушить задачу: /artifacts пуст. "
            "Возвращаю ход Исполнителю. Он должен прислать ПОЛНЫЙ файл строго в формате:\n\n"
            "[FILE: docs/PROJECT_AUDIT.md]\n```md\n# Реальное содержимое файла\n```\n\n"
            "Без пояснений вместо файла."
        )

    async def _redirect_to_coordinator_for_final(self, cid, tid, td, reason="Достигнут лимит обсуждения."):
        """Передаёт финализацию координатору, если текущий агент не координатор."""
        await self.redis.setex(f"turn:{cid}:{tid}", 600, "coordinator")
        await self.redis.setex(f"final_reason:{cid}:{tid}", 600, reason)
        await self.redis.setex(f"pending:{cid}:coordinator", 300, f"{tid}:{td}")

    async def _think_and_reply(self, cid, tid, td, hist, steps):
        active = await self.redis.get(f"active_task:{cid}")
        if not active:
            await self._clear_task_runtime_keys(cid, tid)
            return
        if active.decode() != str(tid):
            await self._clear_stale_task_keys(cid, tid)
            return

        step = steps + 1
        prompt = await self._get_prompt_for_role(cid, self.role)
        prompt += CORRECT_USERNAMES_PROMPT + STRUCTURED_OUTPUT_PROMPT
        team = await self._get_task_team(cid, tid)
        task_type = await self._get_task_type(cid, tid)
        budget = await self._get_task_budget(cid, tid)
        ms = int(budget.get("hard", await self._get_max_steps(cid)))
        soft_max_steps = int(budget.get("soft", max(3, ms - 3)))
        prompt += "\n\nАктивная команда для этой задачи: " + ", ".join([f"{r}={AGENT_BOTS[r]['name']}" for r in team])
        prompt += f"\nТип задачи: {task_type}. Бюджет шагов: min={budget.get('min')} soft={budget.get('soft')} hard={budget.get('hard')}."
        prompt += "\nПередавай ход только агентам из активной команды."
        if self.role in ("executor", "architect", "qa"):
            prompt += "\n\nЕсли создаёшь файл для GitHub, используй формат: [FILE: generated_code/example.py] затем fenced code block. Разрешённые папки: generated/, generated_code/, configs/, docs/, artifacts/. Не создавай .env и секреты."
        if self.role == "executor" and self._artifact_required_for_task(td, task_type):
            prompt += "\n\nКРИТИЧЕСКИ ВАЖНО: эта задача требует файл-артефакт. Нельзя отвечать 'файл создан' или описанием. Верни ПОЛНЫЙ файл строго в формате [FILE: path] + ```lang code block```. Без этого задача не может быть завершена или запушена."
        context_block = read_context_files()
        if context_block:
            prompt += context_block
        memory_block = await build_memory_context(cid)
        if memory_block:
            prompt += memory_block
        raw_skills = await self.redis.get(f"task_skills:{cid}:{tid}")
        task_skills = json.loads(raw_skills.decode()) if raw_skills else []
        skills_block = build_skills_context(task_skills)
        if skills_block:
            prompt += skills_block

        min_final_steps = int(budget.get("min", getattr(settings, "MIN_FINAL_STEPS", 6)))
        if step < min_final_steps:
            prompt += f"\n\nШаг {step}/{ms}. РАНО для финального ответа. final=false. Не используй [ФИНАЛЬНЫЙ ОТВЕТ] до шага {min_final_steps}."
        elif self.role == "coordinator" and step < 5:
            prompt += f"\n\nШаг {step}/{ms}. РАНО для финального ответа."
        elif self.role == "coordinator" and step >= soft_max_steps:
            prompt += "\n\nSOFT LIMIT достигнут. Если задача готова — финализируй. Если не готова — задай ровно один недостающий шаг одному агенту. Не запускай новый круг обсуждения."
        elif self.role == "coordinator" and step >= max(6, min_final_steps):
            prompt += "\n\nЕсли данных уже достаточно, заверши обсуждение через [ФИНАЛЬНЫЙ ОТВЕТ]. Не растягивай диалог без необходимости."

        final_reason = await self.redis.get(f"final_reason:{cid}:{tid}")
        if final_reason and self.role == "coordinator":
            prompt += FINALIZATION_PROMPT

        llm_msgs = [{"role": "assistant" if m["role"] != "user" else "user", "content": m["content"]} for m in hist[-10:]]
        model = await self._get_model(cid)
        await add_run_event(self.redis, cid, tid, "agent_turn_started", role=self.role, data={"step": step, "model": model})
        response = await asyncio.to_thread(call_llm_sync, prompt, llm_msgs, td, model, FALLBACK_MODELS)
        if not response:
            fail_key = f"llm_fail:{cid}:{tid}"
            fails = await self.redis.incr(fail_key)
            await self.redis.expire(fail_key, 900)
            logger.warning(f"LLM empty response: role={self.role}, task={tid}, fails={fails}")
            await add_run_event(self.redis, cid, tid, "llm_empty_response", role=self.role, data={"fails": fails})

            if self.role == "coordinator" and (fails >= 2 or step >= ms):
                await self._force_finalize(cid, tid, td, "Модель не вернула ответ или исчерпан лимит шагов.")
                return

            if fails >= 2 or step >= ms:
                await self._redirect_to_coordinator_for_final(cid, tid, td, "Модель не вернула ответ или исчерпан лимит шагов.")
                return

            na = self._next_role_after(self.role, team)
            await self.redis.setex(f"turn:{cid}:{tid}", 600, na)
            await self.redis.setex(f"pending:{cid}:{na}", 300, f"{tid}:{td}")
            return

        await self.redis.delete(f"llm_fail:{cid}:{tid}")
        response, parsed_next_agent, parsed_final, parsed_ok = parse_structured_agent_response(response, current_role=self.role)
        response = normalize_agent_mentions(response)
        logger.info(f"Structured response: role={self.role}, parsed={parsed_ok}, final={parsed_final}, next={parsed_next_agent}")

        # Защита от слишком ранней финализации: модель иногда ставит final=true уже на 2-3 шаге.
        final_requested = parsed_final or is_final_response(response)
        roles_seen = await self._get_roles_seen(cid, tid)
        required_roles = [r for r in required_roles_before_final() if r in team]
        missing_roles = [r for r in required_roles if r not in roles_seen]
        required_ok = not missing_roles
        incomplete_final = final_requested and (
            is_incomplete_final_response(response)
            or asks_another_agent_to_continue(response, current_role=self.role)
            or bool(parsed_next_agent)
        )
        ready = await self._task_readiness(cid, tid, task_type, team)
        history_for_stagnation = await self._get_history(cid, tid)
        stagnation = self._detect_stagnation(history_for_stagnation)
        hard_limit_reached = int(steps) >= max(0, ms - 1)
        soft_limit_reached = step >= soft_max_steps
        artifact_required = self._artifact_required_for_task(td, task_type)
        artifacts_present = await self._has_artifacts(cid, tid)
        no_required_artifact = artifact_required and not artifacts_present
        if no_required_artifact:
            logger.info(f"Final blocked: artifact required but missing, task={tid}, type={task_type}")
        final_allowed = (
            self.role == "coordinator" and not incomplete_final and not no_required_artifact and (
                bool(final_reason)
                or hard_limit_reached
                or (step >= min_final_steps and ready)
                or (soft_limit_reached and required_ok)
                or (soft_limit_reached and stagnation)
            )
        )
        logger.info(f"Final gate: requested={final_requested}, allowed={final_allowed}, ready={ready}, stagnation={stagnation}, type={task_type}, budget={budget}, incomplete={incomplete_final}, seen={sorted(roles_seen)}, missing={missing_roles}")
        if final_requested and not final_allowed:
            logger.warning(
                f"Early/incomplete final blocked: role={self.role}, step={step}, min={min_final_steps}, task={tid}, missing={missing_roles}, incomplete={incomplete_final}"
            )
            await add_run_event(self.redis, cid, tid, "final_blocked", role=self.role, data={"step": step, "missing": missing_roles, "incomplete": incomplete_final})
            response = strip_final_markers(response)
            if not response:
                response = "Пока рано завершать задачу. Продолжаю обсуждение и передаю ход дальше."
            if no_required_artifact:
                response += "\n\n⚠️ Финализация пока невозможна: задача требует файл/артефакт, но в /artifacts ничего не сохранено. Исполнитель должен выдать файл строго в формате [FILE: path] + code block."
                parsed_next_agent = "executor" if "executor" in team else self._next_role_after(self.role, team)
            elif missing_roles:
                response += f"\n\n⚠️ Финализация пока рано: не высказались обязательные роли: {', '.join(missing_roles)}."
                parsed_next_agent = missing_roles[0]
            elif incomplete_final:
                response += "\n\n⚠️ Это ещё не финальный ответ: в сообщении есть признаки незавершённой проверки/валидации. Передаю ход дальше."
                parsed_next_agent = detect_next_agent(response, current_role=self.role) or "qa"
            else:
                response += f"\n\n⚠️ Финализация пока рано: шаг {step}/{ms}. Продолжаем обсуждение."
            parsed_final = False
            if not parsed_next_agent or parsed_next_agent == self.role:
                parsed_next_agent = self._next_role_after(self.role, team)

        if final_allowed and self.role == "coordinator" and not final_requested:
            logger.info(f"Auto-finalizing ready task: type={task_type}, step={step}, budget={budget}")
            await add_run_event(self.redis, cid, tid, "auto_finalized", role=self.role, data={"step": step, "type": task_type, "ready": ready})
            response = "[ФИНАЛЬНЫЙ ОТВЕТ]\n" + strip_final_markers(response)
            parsed_final = True

        if no_required_artifact and soft_limit_reached and self.role == "coordinator":
            await self._handle_missing_required_artifact(cid, tid, td, "soft/hard limit reached but no artifact")
            return

        if self.role == "executor" and self._artifact_required_for_task(td, task_type) and "[FILE:" not in response:
            await add_run_event(self.redis, cid, tid, "executor_no_file_marker", role=self.role, data={"step": step})
            response += "\n\n⚠️ Для этой задачи требуется файл. Исполнитель должен вернуть не описание, а полный блок [FILE: path] + code block."
            parsed_next_agent = "executor"

        if self.role in ("executor", "architect", "qa"):
            artifacts = extract_artifacts_from_text(response, role=self.role)
            if artifacts:
                await save_artifacts(self.redis, cid, tid, artifacts)
                await add_run_event(self.redis, cid, tid, "artifacts_saved", role=self.role, data={"count": len(artifacts), "files": [a.path for a in artifacts]})
                await self.bot.send_message(cid, f"📦 Найдено артефактов для GitHub: {len(artifacts)}")
            elif self.role == "executor" and self._artifact_required_for_task(td, task_type):
                await add_run_event(self.redis, cid, tid, "artifact_parse_failed", role=self.role, data={"step": step, "has_file_marker": "[FILE:" in response})
                # Show executor response, but route back to executor via coordinator warning below.
                response += "\n\n⚠️ Artifact не сохранён: нужен полный [FILE: path] с содержимым файла в code block."
                parsed_next_agent = "coordinator"

        sr = ""
        for q in re.findall(r'\[SEARCH:\s*(.+?)\]', response):
            r = await asyncio.to_thread(search_web, q)
            if r:
                sr += f"\n🔎 {q}:\n{r}"
        msg = f"{self.config['emoji']} {response}"
        if sr:
            msg += sr
        try:
            await self._send_long_message(cid, msg)
        except Exception:
            try:
                await self._send_long_message(cid, re.sub(r'<[^>]+>', '', msg))
            except Exception:
                pass
        delay = await self._get_delay(cid)
        await self.redis.setex(f"rate:{self.role}:{cid}", delay * 2, str(time.time()))
        new_steps = await self.redis.incr(f"steps:{cid}:{tid}")
        try:
            dbid = await self.redis.get(f"db_task_id:{cid}:{tid}")
            if dbid:
                await update_task(int(dbid.decode()), current_step=int(new_steps), status=TaskStatus.IN_PROGRESS)
        except Exception as e:
            logger.warning(f"DB task step update failed: {str(e)[:120]}")
        await self._save_message(cid, tid, self.config["name"], msg)
        await mark_plan_role_done(self.redis, cid, tid, self.role)
        await add_run_event(self.redis, cid, tid, "agent_message", role=self.role, data={"step": int(new_steps), "next": parsed_next_agent, "final": bool(parsed_final)})
        if (parsed_final or is_final_response(response)) and final_allowed:
            if settings.GITHUB_AUTO_PUSH:
                artifacts_before_push = await load_artifacts(self.redis, cid, tid)
                if not artifacts_before_push:
                    await self._recover_artifacts_from_history(cid, tid)
                    artifacts_before_push = await load_artifacts(self.redis, cid, tid)
                if artifacts_before_push:
                    await self._push_current_task(cid)
                else:
                    await self.bot.send_message(
                        cid,
                        "⚠️ GitHub auto-push пропущен: в задаче нет сохранённых артефактов. "
                        "Попроси Исполнителя выдать файл в формате [FILE: path] + code block, затем проверь /artifacts и выполни /push."
                    )
            await self._complete_task(cid, tid, "✅ Финальный ответ получен. Задача закрыта, дальнейшие ходы остановлены.", response)
            return

        if int(new_steps) >= ms:
            if self._artifact_required_for_task(td, task_type) and not await self._has_artifacts(cid, tid):
                await self._handle_missing_required_artifact(cid, tid, td, "hard limit reached but no artifact")
                return
            if self.role == "coordinator":
                await self._force_finalize(cid, tid, td, "Достигнут лимит шагов обсуждения.")
            else:
                await self._redirect_to_coordinator_for_final(cid, tid, td, "Достигнут лимит шагов обсуждения.")
            return

        na = parsed_next_agent
        if na not in team:
            na = None
        if not na:
            detected = detect_next_agent(response, current_role=self.role)
            na = detected if detected in team else None
        if not na:
            na = self._next_role_after(self.role, team)
        if na == self.role:
            na = self._next_role_after(self.role, team)
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
        try:
            dbid = await self.redis.get(f"db_task_id:{cid}:{tid}")
            if dbid:
                await add_message(int(dbid.decode()), str(sender), str(text), msg_type="multibot")
        except Exception as e:
            logger.warning(f"DB message save failed: {str(e)[:120]}")

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
                        await self._clear_task_runtime_keys(cid, tid)
                        continue
                    if active.decode() != str(tid):
                        await self._clear_stale_task_keys(cid, tid)
                        continue
                    team = await self._get_task_team(cid, tid)
                    if self.role not in team:
                        continue
                    ct = await self.redis.get(f"turn:{cid}:{tid}")
                    if ct and ct.decode() != self.role:
                        if ct.decode() not in team and self.role == team[0]:
                            await self.redis.setex(f"turn:{cid}:{tid}", 600, self.role)
                        else:
                            continue
                    delay = await self._get_delay(cid)
                    rk = f"rate:{self.role}:{cid}"
                    last = await self.redis.get(rk)
                    if last and (time.time() - float(last)) < delay:
                        await asyncio.sleep(delay)
                    history = await self._get_history(cid, tid)
                    sr = await self.redis.get(f"steps:{cid}:{tid}")
                    steps = int(sr) if sr else 0
                    budget = await self._get_task_budget(cid, tid)
                    ms = int(budget.get("hard", await self._get_max_steps(cid)))
                    got_lock = await self._acquire_task_lock(cid, tid)
                    if not got_lock:
                        logger.info(f"Task lock busy: chat={cid}, task={tid}, role={self.role}")
                        continue
                    try:
                        if steps >= ms:
                            task_type = await self._get_task_type(cid, tid)
                            if self._artifact_required_for_task(td, task_type) and not await self._has_artifacts(cid, tid):
                                await self._handle_missing_required_artifact(cid, tid, td, "poll hard limit reached but no artifact")
                            elif self.role == "coordinator":
                                await self._force_finalize(cid, tid, td, "Достигнут лимит шагов обсуждения.")
                            else:
                                await self._redirect_to_coordinator_for_final(cid, tid, td, "Достигнут лимит шагов обсуждения.")
                            continue
                        await self._think_and_reply(cid, tid, td, history, steps)
                    finally:
                        await self._release_task_lock(cid, tid)
            except Exception as e:
                logger.error(f"Poll error: {e}")

    async def stop(self):
        await self.bot.session.close()
