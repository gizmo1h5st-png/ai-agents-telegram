from pathlib import Path
from typing import List, Dict

TRADING_DIR = Path(__file__).resolve().parent
PROFILES_DIR = TRADING_DIR / "profiles"
STRATEGIES_DIR = TRADING_DIR / "strategies"
RISK_DIR = TRADING_DIR / "risk"

TRADING_STRATEGIES = {
    "liquidity_sweep": {
        "name": "💧 Liquidity Sweep",
        "file": "liquidity_sweep.md",
        "keywords": ["sweep", "ликвид", "high", "low", "вынос", "снятие"],
    },
    "volume_spike": {
        "name": "📊 Volume Spike",
        "file": "volume_spike.md",
        "keywords": ["volume", "объем", "объём", "spike", "аномальный"],
    },
    "fvg": {
        "name": "⚡ FVG",
        "file": "fvg.md",
        "keywords": ["fvg", "fair value gap", "imbalance", "дисбаланс"],
    },
    "order_blocks": {
        "name": "🧱 Order Blocks",
        "file": "order_blocks.md",
        "keywords": ["order block", "ob", "ордер блок", "зона"],
    },
}

ROLE_PROFILE_FILES = {
    "coordinator": "coordinator.md",
    "researcher": "researcher.md",
    "architect": "architect.md",
    "executor": "executor.md",
    "qa": "qa.md",
    "critic": "critic.md",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def list_trading_strategies() -> Dict[str, dict]:
    return TRADING_STRATEGIES


def read_trading_profile(role: str) -> str:
    fname = ROLE_PROFILE_FILES.get(role)
    if not fname:
        return ""
    return _read(PROFILES_DIR / fname)


def read_strategy(strategy_id: str) -> str:
    meta = TRADING_STRATEGIES.get(strategy_id)
    if not meta:
        return ""
    return _read(STRATEGIES_DIR / meta["file"])


def read_risk_rules() -> str:
    return _read(RISK_DIR / "default_risk_rules.md")


def select_strategies_for_text(text: str, enabled: List[str] | None = None) -> List[str]:
    t = (text or "").lower()
    enabled_set = set(enabled) if enabled else set(TRADING_STRATEGIES.keys())
    selected = []
    for sid, meta in TRADING_STRATEGIES.items():
        if sid not in enabled_set:
            continue
        if any(k.lower() in t for k in meta.get("keywords", [])):
            selected.append(sid)
    return selected or [sid for sid in TRADING_STRATEGIES if sid in enabled_set]


def build_trading_context(role: str, task_text: str, enabled_strategies: List[str] | None = None) -> str:
    profile = read_trading_profile(role)
    strategies = select_strategies_for_text(task_text, enabled=enabled_strategies)
    parts = []
    if profile:
        parts.append(f"## Trading Role Profile\n{profile}")
    for sid in strategies:
        body = read_strategy(sid)
        if body:
            parts.append(f"## Strategy: {TRADING_STRATEGIES[sid]['name']}\n{body}")
    risk = read_risk_rules()
    if risk:
        parts.append(f"## Risk Rules\n{risk}")
    if not parts:
        return ""
    return "\n\nTRADING MODE CONTEXT:\n" + "\n\n---\n\n".join(parts)

