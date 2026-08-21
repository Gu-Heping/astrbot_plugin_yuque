"""Chat scope access control helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def normalize_group_ids(raw: Any) -> frozenset[str]:
    """Normalize comma-separated or list-like group IDs into unique strings."""

    if raw is None:
        values: Iterable[Any] = ()
    elif isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, Iterable):
        values = raw
    else:
        values = ()

    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        group_id = str(value or "").strip()
        if not group_id or group_id in seen:
            continue
        seen.add(group_id)
        normalized.append(group_id)
    return frozenset(normalized)


def event_group_id(event: Any) -> str:
    """Return the current event group ID, or an empty string for private chats."""

    private_checker = getattr(event, "is_private_chat", None)
    if callable(private_checker):
        try:
            if private_checker():
                return ""
        except Exception:
            pass

    getter = getattr(event, "get_group_id", None)
    if not callable(getter):
        return ""
    return str(getter() or "").strip()


def is_group_chat(event: Any) -> bool:
    """Return whether this event should be treated as a group chat."""

    private_checker = getattr(event, "is_private_chat", None)
    if callable(private_checker):
        try:
            if private_checker():
                return False
        except Exception:
            pass

    group_checker = getattr(event, "is_group_chat", None)
    if callable(group_checker):
        try:
            return bool(group_checker())
        except Exception:
            pass

    return bool(event_group_id(event))


def is_group_chat_allowed(
    event: Any,
    *,
    whitelist_enabled: bool,
    allowed_group_ids: frozenset[str],
) -> bool:
    """Return whether this event is allowed by the group whitelist."""

    group_id = event_group_id(event)
    if not whitelist_enabled:
        return True
    if not group_id:
        return not is_group_chat(event)
    return group_id in allowed_group_ids


def suppress_default_llm(event: Any) -> None:
    """Prevent AstrBot from falling back to its default LLM for this event."""

    setter = getattr(event, "should_call_llm", None)
    if callable(setter):
        setter(True)
        return

    try:
        event.call_llm = True
    except Exception:
        pass
