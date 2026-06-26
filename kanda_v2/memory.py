"""Simple JSON-based memory for KANDA — stores user info, facts, reminders."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_FILE = Path(__file__).parent / "memory.json"


def _load() -> dict:
    try:
        return json.loads(MEMORY_FILE.read_text())
    except Exception:
        return {"user": {}, "facts": {}, "reminders": []}


def _save(data: dict) -> None:
    MEMORY_FILE.write_text(json.dumps(data, indent=2))


def get_user_name() -> str:
    data = _load()
    return data.get("user", {}).get("name", "friend")


def set_user_info(key: str, value: str) -> None:
    data = _load()
    data.setdefault("user", {})[key] = value
    _save(data)
    logger.info(f"[memory] stored user.{key} = {value}")


def get_user_info(key: str) -> str | None:
    data = _load()
    return data.get("user", {}).get(key)


def store_fact(key: str, value: str) -> None:
    data = _load()
    data.setdefault("facts", {})[key] = value
    _save(data)
    logger.info(f"[memory] stored fact: {key} = {value}")


def get_fact(key: str) -> str | None:
    data = _load()
    return data.get("facts", {}).get(key)


def add_reminder(text: str) -> None:
    data = _load()
    data.setdefault("reminders", []).append(text)
    _save(data)
    logger.info(f"[memory] added reminder: {text}")


def get_reminders() -> list[str]:
    data = _load()
    return data.get("reminders", [])


def remove_reminder(keyword: str) -> str | None:
    """Remove first reminder containing keyword. Returns removed text or None."""
    data = _load()
    reminders = data.get("reminders", [])
    kw = keyword.lower()
    for i, r in enumerate(reminders):
        if kw in r.lower():
            removed = reminders.pop(i)
            _save(data)
            logger.info(f"[memory] removed reminder: {removed}")
            return removed
    return None


def clear_reminders() -> None:
    data = _load()
    data["reminders"] = []
    _save(data)


def get_context_for_llm() -> str:
    """Return memory context string to inject into LLM prompts."""
    data = _load()
    lines = []
    user = data.get("user", {})
    if user.get("name"):
        lines.append(f"User's name: {user['name']}")
    for k, v in user.items():
        if k != "name":
            lines.append(f"User {k}: {v}")
    facts = data.get("facts", {})
    for k, v in facts.items():
        lines.append(f"Fact - {k}: {v}")
    reminders = data.get("reminders", [])
    if reminders:
        lines.append(f"Reminders: {', '.join(reminders)}")
    return "\n".join(lines) if lines else ""
