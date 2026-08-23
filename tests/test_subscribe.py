import pytest

from novabot.subscribe import SubscriptionManager


class _Storage:
    def __init__(self, data_dir):
        self.data_dir = data_dir


@pytest.mark.asyncio
async def test_subscribe_deduplicates_by_chat_origin(tmp_path):
    manager = SubscriptionManager(_Storage(tmp_path))

    first_success, first_msg = await manager.subscribe(
        "user-a",
        "group-1",
        "all",
    )
    second_success, second_msg = await manager.subscribe(
        "user-b",
        "group-1",
        "all",
    )

    assert first_success is True
    assert "订阅成功" in first_msg
    assert second_success is False
    assert second_msg == "当前会话已订阅此项"
    assert len(manager.get_all_subscriptions()) == 1


@pytest.mark.asyncio
async def test_subscribe_allows_same_scope_in_different_chats(tmp_path):
    manager = SubscriptionManager(_Storage(tmp_path))

    first_success, _ = await manager.subscribe("user-a", "group-1", "all")
    second_success, _ = await manager.subscribe("user-a", "group-2", "all")

    assert first_success is True
    assert second_success is True
    assert len(manager.get_all_subscriptions()) == 2


def test_get_subscribers_deduplicates_legacy_group_duplicates(tmp_path):
    manager = SubscriptionManager(_Storage(tmp_path))
    manager.subscriptions_file.write_text(
        """
        {
          "next_id": 4,
          "subscriptions": [
            {"id": 1, "platform_id": "user-a", "umo": "group-1", "sub_type": "all", "target": null},
            {"id": 2, "platform_id": "user-b", "umo": "group-1", "sub_type": "all", "target": null},
            {"id": 3, "platform_id": "user-c", "umo": "group-2", "sub_type": "all", "target": null}
          ]
        }
        """,
        encoding="utf-8",
    )

    subscribers = manager.get_subscribers({"book_name": "工程", "author": "Alice"})

    assert sorted(subscribers) == [("group-1", "user-a"), ("group-2", "user-c")]
