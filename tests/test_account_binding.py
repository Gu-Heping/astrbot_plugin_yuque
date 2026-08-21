from __future__ import annotations

from novabot.account_binding import bind_yuque_account, unbind_yuque_account


class _Storage:
    def __init__(self, *, members=None, bindings=None):
        self.members = members or {}
        self.bindings = bindings or {}
        self.added = []
        self.removed = []

    def get_binding(self, platform_id):
        return self.bindings.get(platform_id)

    def load_members(self):
        return self.members

    def find_member_by_name(self, query):
        query = query.lower()
        for uid, info in self.members.items():
            if query in info.get("name", "").lower() or query in info.get("login", "").lower():
                return {"id": int(uid), **info}
        return None

    def add_binding(self, platform_id, yuque_info):
        self.added.append((platform_id, yuque_info))
        self.bindings[platform_id] = yuque_info

    def remove_binding(self, platform_id):
        self.removed.append(platform_id)
        self.bindings.pop(platform_id, None)


def test_bind_yuque_account_succeeds_with_synced_member():
    storage = _Storage(members={"42": {"name": "张三", "login": "alice"}})

    text = bind_yuque_account(storage=storage, platform_id="qq:1", query="alice")

    assert "✅ 绑定成功" in text
    assert "账号: @alice (张三)" in text
    assert storage.added == [
        (
            "qq:1",
            {
                "yuque_id": 42,
                "yuque_login": "alice",
                "yuque_name": "张三",
            },
        )
    ]


def test_bind_yuque_account_reports_existing_binding():
    storage = _Storage(bindings={"qq:1": {"yuque_login": "alice"}})

    text = bind_yuque_account(storage=storage, platform_id="qq:1", query="bob")

    assert text == "已绑定 @alice\n使用 /unbind 解绑后重新绑定"
    assert storage.added == []


def test_bind_yuque_account_validates_input_and_member_sync():
    empty = bind_yuque_account(storage=_Storage(), platform_id="qq:1", query="")
    too_long = bind_yuque_account(storage=_Storage(), platform_id="qq:1", query="x" * 101)
    unsynced = bind_yuque_account(storage=_Storage(), platform_id="qq:1", query="alice")

    assert "/bind <用户名>" in empty
    assert "用户名过长" in too_long
    assert "团队成员未同步" in unsynced


def test_bind_yuque_account_reports_missing_member_with_samples():
    storage = _Storage(
        members={
            "1": {"name": "Alice", "login": "alice"},
            "2": {"name": "Bob", "login": "bob"},
        }
    )

    text = bind_yuque_account(storage=storage, platform_id="qq:1", query="Carol")

    assert "未找到「Carol」" in text
    assert "成员示例: Alice, Bob" in text
    assert storage.added == []


def test_unbind_yuque_account_removes_existing_binding():
    storage = _Storage(bindings={"qq:1": {"yuque_login": "alice"}})

    text = unbind_yuque_account(storage=storage, platform_id="qq:1")

    assert text == "✅ 已解除绑定 @alice"
    assert storage.removed == ["qq:1"]
    assert storage.bindings == {}


def test_unbind_yuque_account_reports_missing_binding():
    storage = _Storage()

    text = unbind_yuque_account(storage=storage, platform_id="qq:1")

    assert text == "你还没有绑定账号"
    assert storage.removed == []
