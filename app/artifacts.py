import base64
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional, List, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

# Supported formats:
# [FILE: generated_code/example.py]
# ```python
# ...
# ```
#
# Also supports imperfect model output where the closing ``` is missing.
FILE_BLOCK_RE = re.compile(
    r"\[FILE:\s*(?P<path>[^\]]+)\]\s*```(?P<lang>[a-zA-Z0-9_+.-]*)?\s*(?P<content>[\s\S]*?)```",
    re.MULTILINE,
)

FILE_BLOCK_UNCLOSED_RE = re.compile(
    r"\[FILE:\s*(?P<path>[^\]]+)\]\s*```(?P<lang>[a-zA-Z0-9_+.-]*)?\s*(?P<content>[\s\S]*)$",
    re.MULTILINE,
)

ARTIFACTS_TTL = 60 * 60 * 24 * 7
MAX_ARTIFACT_BYTES = 300_000


@dataclass
class Artifact:
    path: str
    content: str
    role: str
    created_at: float
    base_sha: Optional[str] = None


def _allowed_prefixes() -> list[str]:
    raw = getattr(settings, "GITHUB_ALLOWED_PREFIXES", "generated/,generated_code/,configs/,docs/,artifacts/")
    return [x.strip() for x in raw.split(",") if x.strip()]


def validate_artifact_path(path: str) -> str:
    path = (path or "").strip().replace("\\", "/")
    if not path:
        raise ValueError("Empty artifact path")

    p = PurePosixPath(path)
    if p.is_absolute():
        raise ValueError("Absolute paths are not allowed")
    if ".." in p.parts:
        raise ValueError("Path traversal is not allowed")

    forbidden_names = {
        ".env", ".env.local", ".env.production", ".envrc",
        "id_rsa", "id_ed25519", "known_hosts",
        "secrets.json", "credentials.json", "token.txt",
    }
    if p.name.lower() in forbidden_names:
        raise ValueError(f"Forbidden file name: {p.name}")

    allowed = _allowed_prefixes()
    if allowed and not any(str(p).startswith(prefix) for prefix in allowed):
        raise ValueError(f"Path must start with one of: {', '.join(allowed)}")

    return str(p)


def _normalize_artifact_content(content: str) -> str:
    """Normalize content captured from Telegram/LLM output.

    Telegram copies often show HTML as &lt;...&gt;. For artifacts we want real file content.
    Also removes accidental trailing prose after an unclosed code block when possible.
    """
    content = content or ""
    content = html.unescape(content)

    # If model forgot closing fence and then continued with normal prose, cut common markers.
    stop_markers = [
        "\n\nQA:", "\n\nКритик", "\n\nАрхитектор", "\n\nИсполнитель",
        "\n\nПроверка", "\n\nВывод", "\n\nПередаю", "\n\n```",
    ]
    cut_at = None
    for marker in stop_markers:
        idx = content.find(marker)
        if idx != -1:
            cut_at = idx if cut_at is None else min(cut_at, idx)
    if cut_at is not None:
        content = content[:cut_at]

    return content.strip() + "\n"


def _iter_file_matches(text: str) -> List[Tuple[str, str]]:
    """Return list of (path, content) from closed and unclosed FILE blocks."""
    text = text or ""
    matches: List[Tuple[str, str]] = []
    spans = []

    for match in FILE_BLOCK_RE.finditer(text):
        matches.append((match.group("path"), match.group("content") or ""))
        spans.append(match.span())

    # If there is no closed block for a [FILE:] occurrence, try unclosed fallback.
    # This fixes common LLM output where final ``` is missing.
    for match in FILE_BLOCK_UNCLOSED_RE.finditer(text):
        span = match.span()
        if any(not (span[1] <= s[0] or span[0] >= s[1]) for s in spans):
            continue
        matches.append((match.group("path"), match.group("content") or ""))

    return matches


def extract_artifacts_from_text(text: str, role: str) -> List[Artifact]:
    artifacts: List[Artifact] = []
    seen_paths = set()

    for raw_path, raw_content in _iter_file_matches(text):
        try:
            safe_path = validate_artifact_path(raw_path)
        except ValueError as e:
            logger.warning(f"Artifact ignored: path={raw_path!r}, reason={e}")
            continue

        content = _normalize_artifact_content(raw_content)
        if not content.strip():
            continue
        if len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            logger.warning(f"Artifact ignored: {safe_path} too large")
            continue

        # One message may include duplicate path; keep the latest occurrence.
        if safe_path in seen_paths:
            artifacts = [a for a in artifacts if a.path != safe_path]
        seen_paths.add(safe_path)

        artifacts.append(
            Artifact(
                path=safe_path,
                content=content,
                role=role,
                created_at=time.time(),
            )
        )

    if text and "[FILE:" in text and not artifacts:
        logger.warning("FILE marker detected, but no valid artifact extracted")

    return artifacts


async def save_artifacts(redis, cid: int, tid: int, artifacts: List[Artifact]):
    if not artifacts:
        return
    key = f"artifacts:{cid}:{tid}"
    for artifact in artifacts:
        item = {
            "path": artifact.path,
            "content_b64": base64.b64encode(artifact.content.encode("utf-8")).decode("ascii"),
            "role": artifact.role,
            "created_at": artifact.created_at,
            "base_sha": artifact.base_sha,
        }
        await redis.rpush(key, json.dumps(item, ensure_ascii=False))
    await redis.expire(key, ARTIFACTS_TTL)


async def load_artifacts(redis, cid: int, tid: int) -> List[Artifact]:
    key = f"artifacts:{cid}:{tid}"
    raw_items = await redis.lrange(key, 0, -1)
    artifacts: List[Artifact] = []
    seen = set()

    # Latest version wins for duplicate paths.
    for raw in reversed(raw_items):
        try:
            data = json.loads(raw)
            path = data["path"]
            if path in seen:
                continue
            seen.add(path)
            content = base64.b64decode(data["content_b64"]).decode("utf-8")
            artifacts.append(
                Artifact(
                    path=path,
                    content=content,
                    role=data.get("role", "unknown"),
                    created_at=data.get("created_at", time.time()),
                    base_sha=data.get("base_sha"),
                )
            )
        except Exception as e:
            logger.warning(f"Bad artifact record ignored: {str(e)[:120]}")
    return list(reversed(artifacts))


async def clear_artifacts(redis, cid: int, tid: int):
    await redis.delete(f"artifacts:{cid}:{tid}")


def format_artifacts(artifacts: List[Artifact]) -> str:
    if not artifacts:
        return "📦 Артефактов пока нет.\n\nЧтобы агент создал файл, он должен использовать формат:\n<code>[FILE: generated_code/example.py]</code>"
    lines = ["📦 <b>Артефакты задачи</b>\n"]
    for idx, artifact in enumerate(artifacts, start=1):
        size = len(artifact.content.encode("utf-8"))
        lines.append(f"{idx}. <code>{artifact.path}</code> · {size} bytes · role=<code>{artifact.role}</code>")
    return "\n".join(lines)
