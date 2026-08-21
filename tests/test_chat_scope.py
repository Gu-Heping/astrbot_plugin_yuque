from novabot.chat_scope import (
    event_group_id,
    is_group_chat,
    is_group_chat_allowed,
    normalize_group_ids,
    suppress_default_llm,
)


class _Event:
    def __init__(self, group_id=None, *, private=False, group=None):
        self.group_id = group_id
        self.private = private
        self.group = group
        self.should_call_llm_calls = []

    def get_group_id(self):
        return self.group_id

    def is_private_chat(self):
        return self.private

    def is_group_chat(self):
        if self.group is None:
            raise RuntimeError("group type unknown")
        return self.group

    def should_call_llm(self, flag):
        self.should_call_llm_calls.append(flag)


def test_normalize_group_ids_accepts_string_and_iterables():
    assert normalize_group_ids(" 123,456,123,, ") == frozenset({"123", "456"})
    assert normalize_group_ids([123, " 456 ", None, "123"]) == frozenset({"123", "456"})


def test_group_scope_allows_private_and_disabled_whitelist():
    assert event_group_id(_Event("")) == ""
    assert event_group_id(_Event("g1", private=True)) == ""
    assert not is_group_chat(_Event("g1", private=True))
    assert is_group_chat(_Event("g1"))
    assert is_group_chat_allowed(
        _Event("g2"),
        whitelist_enabled=False,
        allowed_group_ids=frozenset(),
    )
    assert is_group_chat_allowed(
        _Event("", group=True),
        whitelist_enabled=False,
        allowed_group_ids=frozenset(),
    )
    assert is_group_chat_allowed(
        _Event(None),
        whitelist_enabled=True,
        allowed_group_ids=frozenset(),
    )
    assert not is_group_chat_allowed(
        _Event("", group=True),
        whitelist_enabled=True,
        allowed_group_ids=frozenset({"g1"}),
    )


def test_group_scope_enforces_enabled_whitelist():
    allowed = frozenset({"g1"})

    assert is_group_chat_allowed(_Event("g1"), whitelist_enabled=True, allowed_group_ids=allowed)
    assert not is_group_chat_allowed(_Event("g2"), whitelist_enabled=True, allowed_group_ids=allowed)
    assert not is_group_chat_allowed(_Event("g1"), whitelist_enabled=True, allowed_group_ids=frozenset())


def test_group_scope_uses_explicit_group_checker_when_available():
    assert is_group_chat(_Event("", group=True))
    assert not is_group_chat(_Event("g1", group=False))


def test_suppress_default_llm_uses_astrbot_setter():
    event = _Event()

    suppress_default_llm(event)

    assert event.should_call_llm_calls == [True]
