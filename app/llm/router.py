import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Any, Tuple

import httpx

from app.config import settings, FREE_MODELS

logger = logging.getLogger(__name__)

# In-process cache (for Railway single-replica)
_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_MAX_SIZE = 150
_CACHE_TTL_SECONDS = 60 * 30
_CACHE_TS: Dict[str, float] = {}

# Circuit breaker
_PROVIDER_BLOCKED_UNTIL: Dict[str, float] = {}
_PROVIDER_LAST_ERROR: Dict[str, str] = {}
_PROVIDER_STATS: Dict[str, Dict[str, int]] = {}


OPENAI_COMPATIBLE_PROVIDERS = {
    "mistral",
    "openrouter",
    "huggingface",
    "groq",
    "cerebras",
}


def _stat(provider: str, key: str):
    _PROVIDER_STATS.setdefault(provider, {"success": 0, "fail": 0, "skip": 0})
    _PROVIDER_STATS[provider][key] = _PROVIDER_STATS[provider].get(key, 0) + 1


def _mask(value: str) -> str:
    if not value:
        return "not_set"
    if len(value) <= 8:
        return "set"
    return f"{value[:4]}...{value[-4:]}"


def _cache_key(system_prompt: str, messages: List[Dict[str, Any]], task: str, model: str) -> str:
    payload = {
        "model": model,
        "system": system_prompt[:1500],
        "messages": messages[-8:],
        "task": task[:1200],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[str]:
    ts = _CACHE_TS.get(key)
    if not ts:
        return None
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        _CACHE_TS.pop(key, None)
        return None
    value = _CACHE.get(key)
    if value is not None:
        _CACHE.move_to_end(key)
    return value


def _cache_set(key: str, value: str):
    _CACHE[key] = value
    _CACHE_TS[key] = time.time()
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX_SIZE:
        old_key, _ = _CACHE.popitem(last=False)
        _CACHE_TS.pop(old_key, None)


def get_provider_for_model(model_id: str) -> str:
    for m in FREE_MODELS.values():
        if m["id"] == model_id:
            return m.get("provider", "openrouter")

    if model_id in ("mistral-small-latest", "open-mistral-nemo", "ministral-8b-latest"):
        return "mistral"

    if model_id.startswith(("llama-", "mixtral-", "gemma-", "qwen-")):
        if settings.GROQ_API_KEY:
            return "groq"
        if settings.CEREBRAS_API_KEY:
            return "cerebras"

    if "/" in model_id and ":free" not in model_id:
        known_openrouter_prefixes = (
            "openai/", "deepseek/", "meta-llama/", "mistralai/", "google/",
            "qwen/", "zhipu-ai/", "nousresearch/", "nvidia/", "moonshotai/",
            "x-ai/",
        )
        if not model_id.startswith(known_openrouter_prefixes):
            return "huggingface"

    return "openrouter"


def _provider_config(provider: str) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
    if provider == "mistral":
        if not settings.MISTRAL_API_KEY:
            return None, None
        return f"{settings.MISTRAL_BASE_URL}/chat/completions", {
            "Authorization": f"Bearer {settings.MISTRAL_API_KEY}",
            "Content-Type": "application/json",
        }

    if provider == "openrouter":
        if not settings.OPENROUTER_API_KEY:
            return None, None
        return f"{settings.OPENROUTER_BASE_URL}/chat/completions", {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/gizmo1h5st-png/ai-agents-telegram",
            "X-Title": "AI Agents Telegram",
        }

    if provider == "huggingface":
        if not settings.HUGGINGFACE_API_KEY:
            return None, None
        return "https://router.huggingface.co/v1/chat/completions", {
            "Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}",
            "Content-Type": "application/json",
        }

    if provider == "groq":
        if not settings.GROQ_API_KEY:
            return None, None
        return f"{settings.GROQ_BASE_URL}/chat/completions", {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

    if provider == "cerebras":
        if not settings.CEREBRAS_API_KEY:
            return None, None
        return f"{settings.CEREBRAS_BASE_URL}/chat/completions", {
            "Authorization": f"Bearer {settings.CEREBRAS_API_KEY}",
            "Content-Type": "application/json",
        }

    return None, None


def _is_provider_blocked(provider: str) -> bool:
    until = _PROVIDER_BLOCKED_UNTIL.get(provider, 0)
    if until <= time.time():
        _PROVIDER_BLOCKED_UNTIL.pop(provider, None)
        return False
    return True


def _block_provider(provider: str, seconds: int, reason: str):
    _PROVIDER_BLOCKED_UNTIL[provider] = time.time() + seconds
    _PROVIDER_LAST_ERROR[provider] = reason
    logger.warning(f"LLM provider blocked: provider={provider}, seconds={seconds}, reason={reason}")


def _extract_content(data: Dict[str, Any]) -> Optional[str]:
    choices = data.get("choices") or []
    if not choices:
        return None
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        content = "\n".join(parts)
    return content.strip() if content else None


def _finish_reason(data: Dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("finish_reason") or choices[0].get("finishReason") or "")


def _looks_truncated(content: str) -> bool:
    if not content:
        return False
    t = content.rstrip()
    if t.count("```") % 2 == 1:
        return True
    if "[FILE:" in t and "```" in t and not t.endswith("```"):
        return True
    if t.endswith((",", "и", "или", "что", "который", "которые", "<", "</", "```html")):
        return True
    if len(t) > 1000 and t[-1] not in ".!?。)]}>`\n":
        return True
    return False


def _post_chat(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> httpx.Response:
    with httpx.Client(timeout=settings.LLM_REQUEST_TIMEOUT) as client:
        return client.post(url, headers=headers, json=payload)


def _continue_content(url: str, headers: Dict[str, str], payload: Dict[str, Any], content: str, max_rounds: int) -> str:
    combined = content or ""
    rounds = max(0, int(max_rounds or 0))
    if rounds <= 0:
        return combined

    messages = list(payload.get("messages") or [])
    model = payload.get("model")
    max_tokens = payload.get("max_tokens")
    temperature = payload.get("temperature", 0.7)

    for i in range(rounds):
        cont_messages = [
            *messages,
            {"role": "assistant", "content": combined},
            {
                "role": "user",
                "content": (
                    "Продолжи ответ ровно с места обрыва. "
                    "Не повторяй уже написанное. Если это блок кода или [FILE:], обязательно закрой его корректно."
                ),
            },
        ]
        cont_payload = {
            "model": model,
            "messages": cont_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            resp = _post_chat(url, headers, cont_payload)
            if resp.status_code != 200:
                logger.warning(f"LLM continuation failed: status={resp.status_code}, body={resp.text[:160]}")
                break
            data = resp.json()
            extra = _extract_content(data)
            if not extra:
                break
            combined += "\n" + extra.strip()
            fr = _finish_reason(data).lower()
            if fr not in ("length", "max_tokens", "max_tokens_exceeded") and not _looks_truncated(extra):
                break
        except Exception as e:
            logger.warning(f"LLM continuation exception: {str(e)[:160]}")
            break
    return combined


def _models_to_try(preferred_model: str, fallback_models: List[str]) -> List[str]:
    result = []
    for m in [preferred_model] + list(fallback_models or []):
        if m and m not in result:
            result.append(m)
    return result


def call_llm_sync(
    system_prompt: str,
    messages: List[Dict[str, Any]],
    task: str,
    model: str,
    fallback_models: Optional[List[str]] = None,
    max_tokens: Optional[int] = None,
    temperature: float = 0.7,
    use_cache: bool = True,
) -> Optional[str]:
    fallback_models = fallback_models or []
    models_to_try = _models_to_try(model, fallback_models)

    full_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ЗАДАЧА: {task}"},
        *messages,
    ]

    last_error = None
    for try_model in models_to_try[:10]:
        provider = get_provider_for_model(try_model)

        if _is_provider_blocked(provider):
            _stat(provider, "skip")
            continue

        url, headers = _provider_config(provider)
        if not url:
            _stat(provider, "skip")
            continue

        ck = _cache_key(system_prompt, messages, task, try_model)
        if use_cache:
            cached = _cache_get(ck)
            if cached:
                logger.info(f"LLM cache hit: provider={provider}, model={try_model}")
                return cached

        payload = {
            "model": try_model,
            "messages": full_messages,
            "max_tokens": max_tokens or settings.MAX_TOKENS_PER_REQUEST,
            "temperature": temperature,
        }

        try:
            resp = _post_chat(url, headers, payload)

            if resp.status_code == 200:
                data = resp.json()
                content = _extract_content(data)
                if content:
                    finish_reason = _finish_reason(data).lower()
                    if finish_reason in ("length", "max_tokens", "max_tokens_exceeded") or _looks_truncated(content):
                        logger.warning(
                            f"LLM output may be truncated: provider={provider}, model={try_model}, finish_reason={finish_reason}, len={len(content)}"
                        )
                        content = _continue_content(
                            url=url,
                            headers=headers,
                            payload=payload,
                            content=content,
                            max_rounds=getattr(settings, "LLM_CONTINUE_MAX", 2),
                        )
                    _stat(provider, "success")
                    logger.info(f"LLM success: provider={provider}, model={try_model}, finish_reason={finish_reason}")
                    if use_cache:
                        _cache_set(ck, content)
                    return content
                _stat(provider, "fail")
                last_error = f"empty_content {provider}"
                continue

            body_short = resp.text[:240]
            last_error = f"{provider} {resp.status_code} {body_short}"
            _PROVIDER_LAST_ERROR[provider] = last_error
            _stat(provider, "fail")

            if resp.status_code in (401, 403):
                _block_provider(provider, 15 * 60, f"auth {resp.status_code}")
            elif resp.status_code in (402, 429):
                _block_provider(provider, 5 * 60, f"limit {resp.status_code}")
            elif resp.status_code in (404, 503):
                _block_provider(provider, 2 * 60, f"unavailable {resp.status_code}")
            else:
                logger.warning(f"LLM error: provider={provider}, model={try_model}, status={resp.status_code}, body={body_short}")

        except Exception as e:
            last_error = f"{provider} exception {str(e)[:160]}"
            _PROVIDER_LAST_ERROR[provider] = last_error
            _stat(provider, "fail")
            _block_provider(provider, 60, "exception")
            logger.warning(f"LLM exception: provider={provider}, model={try_model}, error={str(e)[:160]}")

    logger.error(f"All LLM providers failed. Last error: {last_error}")
    return None


def get_llm_router_status() -> Dict[str, Any]:
    now = time.time()
    providers = {}
    for provider in ["mistral", "openrouter", "huggingface", "groq", "cerebras"]:
        if provider == "mistral":
            key = settings.MISTRAL_API_KEY
        elif provider == "openrouter":
            key = settings.OPENROUTER_API_KEY
        elif provider == "huggingface":
            key = settings.HUGGINGFACE_API_KEY
        elif provider == "groq":
            key = settings.GROQ_API_KEY
        elif provider == "cerebras":
            key = settings.CEREBRAS_API_KEY
        else:
            key = ""

        blocked_until = _PROVIDER_BLOCKED_UNTIL.get(provider, 0)
        providers[provider] = {
            "key": _mask(key),
            "configured": bool(key),
            "blocked_seconds": max(0, int(blocked_until - now)),
            "last_error": _PROVIDER_LAST_ERROR.get(provider, ""),
            "stats": _PROVIDER_STATS.get(provider, {"success": 0, "fail": 0, "skip": 0}),
        }

    return {
        "cache_size": len(_CACHE),
        "cache_ttl_seconds": _CACHE_TTL_SECONDS,
        "providers": providers,
    }
