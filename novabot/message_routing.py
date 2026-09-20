"""Message routing helpers independent from the AstrBot runtime."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


CLAIM_MARKER = "_novabot_message_claimed"
COMMAND_FILTER_NAMES = frozenset({"CommandFilter", "CommandGroupFilter"})


def raw_message_text(event: Any) -> str:
    """Return plain text before AstrBot strips the command wake prefix."""
    message_obj = getattr(event, "message_obj", None)
    components = getattr(message_obj, "message", None)
    if isinstance(components, Iterable) and not isinstance(components, (str, bytes)):
        parts = []
        for component in components:
            text = getattr(component, "text", None)
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "".join(parts).strip()
    return str(getattr(event, "message_str", "") or "").strip()


def has_matched_registered_command(event: Any) -> bool:
    """Return whether AstrBot matched any registered command handler."""
    get_extra = getattr(event, "get_extra", None)
    if not callable(get_extra):
        return False

    try:
        if get_extra("astrbot_known_wake_prefix_command") is True:
            return True
    except TypeError:
        pass

    try:
        parsed = get_extra("handlers_parsed_params")
    except TypeError:
        parsed = None
    if isinstance(parsed, dict) and parsed:
        return True

    try:
        activated = get_extra("activated_handlers")
    except TypeError:
        activated = None
    if not isinstance(activated, (list, tuple)):
        return False

    for handler in activated:
        for event_filter in getattr(handler, "event_filters", ()) or ():
            if type(event_filter).__name__ in COMMAND_FILTER_NAMES:
                return True
    return False


def is_unknown_slash_message(event: Any, original: str) -> bool:
    """Return whether a slash-prefixed message matched no command handler."""
    return original.lstrip().startswith("/") and not has_matched_registered_command(event)


def is_self_message(event: Any) -> bool:
    """Return whether the adapter identifies the sender as the bot itself."""
    sender_getter = getattr(event, "get_sender_id", None)
    self_getter = getattr(event, "get_self_id", None)
    if not callable(sender_getter) or not callable(self_getter):
        return False
    sender_id = str(sender_getter() or "").strip()
    self_id = str(self_getter() or "").strip()
    return bool(sender_id and self_id and sender_id == self_id)


def is_claimed(event: Any) -> bool:
    return bool(getattr(event, CLAIM_MARKER, False))


def mark_claimed(event: Any) -> None:
    setattr(event, CLAIM_MARKER, True)
