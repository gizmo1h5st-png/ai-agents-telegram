from pathlib import Path
from typing import Dict, List

SKILLS_DIR = Path(__file__).resolve().parent / "builtin"
CONTEXT_DIR = Path(__file__).resolve().parent.parent / "context"

SKILL_REGISTRY = {
    "telegram_debug": {
        "name": "🤖 Telegram Debug",
        "file": "telegram_debug.md",
        "keywords": ["telegram", "bot", "бот", "polling", "webhook", "getupdates", "aiogram"],
    },
    "railway_debug": {
        "name": "🚂 Railway Debug",
        "file": "railway_debug.md",
        "keywords": ["railway", "deploy", "container", "redis", "postgres", "env", "logs"],
    },
    "architecture_review": {
        "name": "🏗️ Architecture Review",
        "file": "architecture_review.md",
        "keywords": ["архитект", "architecture", "проектир", "сервис", "система", "api", "инфраструкт"],
    },
    "qa_checklist": {
        "name": "🧪 QA Checklist",
        "file": "qa_checklist.md",
        "keywords": ["qa", "test", "тест", "провер", "edge", "acceptance", "приём"],
    },
    "llm_router_debug": {
        "name": "🧩 LLM Router Debug",
        "file": "llm_router_debug.md",
        "keywords": ["llm", "openrouter", "mistral", "huggingface", "provider", "модель", "429", "402", "404"],
    },
    "github_artifacts": {
        "name": "📦 GitHub Artifacts",
        "file": "github_artifacts.md",
        "keywords": ["github", "commit", "push", "файл", "код", "artifact", "артефакт"],
    },
}

CONTEXT_FILES = ["PROJECT.md", "AGENTS.md", "DEPLOYMENT.md"]


def list_skills() -> Dict[str, dict]:
    return SKILL_REGISTRY

def read_skill(skill_id: str) -> str:
    meta = SKILL_REGISTRY.get(skill_id)
    if not meta:
        return ""
    path = SKILLS_DIR / meta["file"]
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")

def select_skills_for_task(task: str, enabled: List[str] | None = None, limit: int = 4) -> List[str]:
    text = (task or "").lower()
    enabled_set = set(enabled) if enabled else set(SKILL_REGISTRY.keys())
    scored = []
    for sid, meta in SKILL_REGISTRY.items():
        if sid not in enabled_set:
            continue
        score = sum(1 for kw in meta.get("keywords", []) if kw.lower() in text)
        if score:
            scored.append((score, sid))
    scored.sort(reverse=True)
    return [sid for _score, sid in scored[:limit]]

def build_skills_context(skill_ids: List[str]) -> str:
    parts = []
    for sid in skill_ids:
        meta = SKILL_REGISTRY.get(sid, {})
        body = read_skill(sid)
        if body:
            parts.append(f"## Skill: {meta.get('name', sid)}\n{body}")
    if not parts:
        return ""
    return "\n\nНАВЫКИ, РЕЛЕВАНТНЫЕ ЗАДАЧЕ:\n" + "\n\n---\n\n".join(parts)

def read_context_files() -> str:
    parts = []
    for fname in CONTEXT_FILES:
        path = CONTEXT_DIR / fname
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"## {fname}\n{content}")
    if not parts:
        return ""
    return "\n\nПОСТОЯННЫЙ КОНТЕКСТ ПРОЕКТА:\n" + "\n\n---\n\n".join(parts)
