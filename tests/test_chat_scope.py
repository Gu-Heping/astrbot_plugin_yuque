from novabot.chat_scope import event_group_id, is_group_chat_allowed, normalize_group_ids


class _Event:
    def __init__(self, group_id=None):
        self.group_id = group_id

    def get_group_id(self):
        return self.group_id


def test_normalize_group_ids_accepts_string_and_iterables():
    assert normalize_group_ids(" 123,456,123,, ") == frozenset({"123", "456"})
    assert normalize_group_ids([123, " 456 ", None, "123"]) == frozenset({"123", "456"})


def test_group_scope_allows_private_and_disabled_whitelist():
    assert event_group_id(_Event("")) == ""
    assert is_group_chat_allowed(
        _Event("g2"),
        whitelist_enabled=False,
        allowed_group_ids=frozenset(),
    )
    assert is_group_chat_allowed(
        _Event(None),
        whitelist_enabled=True,
        allowed_group_ids=frozenset(),
    )


def test_group_scope_enforces_enabled_whitelist():
    allowed = frozenset({"g1"})

    assert is_group_chat_allowed(_Event("g1"), whitelist_enabled=True, allowed_group_ids=allowed)
    assert not is_group_chat_allowed(_Event("g2"), whitelist_enabled=True, allowed_group_ids=allowed)
    assert not is_group_chat_allowed(_Event("g1"), whitelist_enabled=True, allowed_group_ids=frozenset())
