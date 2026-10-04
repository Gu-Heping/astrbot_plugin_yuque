"""Message routing helpers independent from the AstrBot runtime."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


CLAIM_MARKER = "_novabot_message_claimed"
COMMAND_FILTER_NAMES = frozenset({"CommandFilter", "CommandGroupFilter"})
NON_CHAT_POST_TYPES = frozenset({"request", "notice", "meta_event"})


def _raw_field(raw: Any, name: str) -> Any:
    getter = getattr(raw, "get", None)
    if callable(getter):
        try:
            return getter(name)
        except (TypeError, AttributeError):
            pass
    return getattr(raw, name, None)


def _normalized_type_name(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def non_chat_event_reason(event: Any) -> str:
    """Return why an event is not user chat content, or an empty string."""
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    post_type = str(_raw_field(raw, "post_type") or "").strip().lower()
    if post_type in NON_CHAT_POST_TYPES:
        return f"post_type:{post_type}"

    type_getter = getattr(event, "get_message_type", None)
    if callable(type_getter):
        try:
            message_type = type_getter()
        except (TypeError, AttributeError, ValueError):
            message_type = None
        type_names = {
            _normalized_type_name(message_type),
            _normalized_type_name(getattr(message_type, "name", None)),
            _normalized_type_name(getattr(message_type, "value", None)),
        }
        if "othermessage" in type_names:
            return "message_type:other"

    text = str(getattr(event, "message_str", "") or "").strip()
    components = getattr(message_obj, "message", None)
    has_components = False
    if isinstance(components, Iterable) and not isinstance(components, (str, bytes)):
        try:
            has_components = len(components) > 0
        except TypeError:
            # Do not consume a one-shot component iterator merely to classify it.
            has_components = True
    if not text and not has_components:
        return "empty_event"
    return ""


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
