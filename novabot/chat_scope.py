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

    getter = getattr(event, "get_group_id", None)
    if not callable(getter):
        return ""
    return str(getter() or "").strip()


def is_group_chat_allowed(
    event: Any,
    *,
    whitelist_enabled: bool,
    allowed_group_ids: frozenset[str],
) -> bool:
    """Return whether this event is allowed by the group whitelist."""

    group_id = event_group_id(event)
    if not group_id:
        return True
    if not whitelist_enabled:
        return True
    return group_id in allowed_group_ids
