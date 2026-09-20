from types import SimpleNamespace

from novabot.message_routing import (
    has_matched_registered_command,
    is_claimed,
    is_self_message,
    is_unknown_slash_message,
    mark_claimed,
    raw_message_text,
)


class _Event:
    def __init__(self, text="hello", *, sender="user", self_id="bot", extras=None):
        self.message_str = text.lstrip("/")
        self.message_obj = SimpleNamespace(message=[SimpleNamespace(text=text)])
        self.sender = sender
        self.self_id = self_id
        self.extras = extras or {}

    def get_sender_id(self):
        return self.sender

    def get_self_id(self):
        return self.self_id

    def get_extra(self, key):
        return self.extras.get(key)


def test_raw_message_text_preserves_prefix_removed_from_processed_text():
    event = _Event("/novabot")

    assert event.message_str == "novabot"
    assert raw_message_text(event) == "/novabot"


def test_registered_command_is_detected_from_activated_handler_metadata():
    command_filter = type("CommandFilter", (), {})()
    handler = SimpleNamespace(event_filters=[command_filter])
    event = _Event("subscriptions clear", extras={"activated_handlers": [handler]})

    assert has_matched_registered_command(event)
    assert not is_unknown_slash_message(event, "/subscriptions clear")


def test_unknown_slash_message_is_not_treated_as_registered_command():
    event = _Event("/does-not-exist", extras={"activated_handlers": []})

    assert not has_matched_registered_command(event)
    assert is_unknown_slash_message(event, raw_message_text(event))


def test_self_message_and_claim_marker_are_detected():
    event = _Event(sender="bot", self_id="bot")

    assert is_self_message(event)
    assert not is_claimed(event)
    mark_claimed(event)
    assert is_claimed(event)
