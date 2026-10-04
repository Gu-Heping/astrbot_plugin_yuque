from types import SimpleNamespace

from novabot.message_routing import (
    has_matched_registered_command,
    is_claimed,
    is_self_message,
    is_unknown_slash_message,
    mark_claimed,
    non_chat_event_reason,
    raw_message_text,
)


class _Event:
    def __init__(
        self,
        text="hello",
        *,
        sender="user",
        self_id="bot",
        extras=None,
        components=None,
        raw=None,
        message_type=None,
    ):
        self.message_str = text.lstrip("/")
        self.message_obj = SimpleNamespace(
            message=[SimpleNamespace(text=text)] if components is None else components,
            raw_message=raw,
        )
        self.sender = sender
        self.self_id = self_id
        self.extras = extras or {}
        self.message_type = message_type

    def get_sender_id(self):
        return self.sender

    def get_self_id(self):
        return self.self_id

    def get_extra(self, key):
        return self.extras.get(key)

    def get_message_type(self):
        return self.message_type


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


def test_friend_request_and_notice_are_non_chat_even_when_typed_private():
    request = _Event("", components=[], raw={"post_type": "request"})
    notice = _Event(
        "",
        components=[SimpleNamespace(type="poke")],
        raw=SimpleNamespace(post_type="notice"),
    )

    assert non_chat_event_reason(request) == "post_type:request"
    assert non_chat_event_reason(notice) == "post_type:notice"


def test_other_and_completely_empty_events_are_non_chat():
    other_type = SimpleNamespace(name="OTHER_MESSAGE", value="OtherMessage")
    other = _Event("system", message_type=other_type)
    empty = _Event("", components=[])

    assert non_chat_event_reason(other) == "message_type:other"
    assert non_chat_event_reason(empty) == "empty_event"


def test_regular_text_and_media_only_messages_remain_chat_events():
    text = _Event("hello", raw={"post_type": "message"})
    image = _Event(
        "",
        components=[SimpleNamespace(type="image")],
        raw={"post_type": "message"},
    )
    file_message = _Event(
        "",
        components=[SimpleNamespace(type="file")],
        raw={"post_type": "message"},
    )

    assert non_chat_event_reason(text) == ""
    assert non_chat_event_reason(image) == ""
    assert non_chat_event_reason(file_message) == ""
