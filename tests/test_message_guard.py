import asyncio
from types import SimpleNamespace

import pytest

from novabot.message_guard import ConversationQueue, MessageDeduplicator


class _Event:
    def __init__(self, message_id="m1", *, umo="qq:private:u1", sender="u1"):
        self.message_obj = SimpleNamespace(message_id=message_id)
        self.unified_msg_origin = umo
        self.sender = sender

    def get_platform_id(self):
        return "qq-adapter"

    def get_sender_id(self):
        return self.sender


def test_message_id_deduplication_and_expiry():
    deduplicator = MessageDeduplicator()
    event = _Event("m1")

    assert not deduplicator.check_and_mark(event, "hello", now=0).duplicate
    assert deduplicator.check_and_mark(event, "changed", now=599).duplicate
    assert not deduplicator.check_and_mark(event, "changed", now=600).duplicate


def test_different_message_ids_with_same_text_are_not_duplicates():
    deduplicator = MessageDeduplicator()

    assert not deduplicator.check_and_mark(_Event("m1"), "hello", now=0).duplicate
    assert not deduplicator.check_and_mark(_Event("m2"), "hello", now=1).duplicate


def test_fallback_identity_only_deduplicates_for_five_seconds():
    deduplicator = MessageDeduplicator()
    event = _Event("")

    first = deduplicator.check_and_mark(event, "hello", now=0)
    duplicate = deduplicator.check_and_mark(event, "hello", now=4.9)
    expired = deduplicator.check_and_mark(event, "hello", now=5)

    assert first.identity.source == "fallback"
    assert not first.duplicate
    assert duplicate.duplicate
    assert not expired.duplicate


@pytest.mark.asyncio
async def test_same_conversation_runs_in_fifo_order_and_cleans_up():
    queue = ConversationQueue(max_pending=8)
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def first():
        async with queue.admit("chat"):
            order.append("first-start")
            entered.set()
            await release.wait()
            order.append("first-end")

    async def later(name):
        async with queue.admit("chat"):
            order.append(name)

    first_task = asyncio.create_task(first())
    await entered.wait()
    second_task = asyncio.create_task(later("second"))
    await asyncio.sleep(0)
    third_task = asyncio.create_task(later("third"))
    await asyncio.sleep(0)
    release.set()

    await asyncio.gather(first_task, second_task, third_task)

    assert order == ["first-start", "first-end", "second", "third"]
    assert queue.active_conversations == 0


@pytest.mark.asyncio
async def test_different_conversations_can_run_concurrently():
    queue = ConversationQueue(max_pending=8)
    both_entered = asyncio.Event()
    entered = set()

    async def run(key):
        async with queue.admit(key):
            entered.add(key)
            if len(entered) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=1)

    await asyncio.gather(run("chat-a"), run("chat-b"))

    assert entered == {"chat-a", "chat-b"}


def test_queue_overflow_notifies_once_per_busy_period():
    queue = ConversationQueue(max_pending=1)

    active = queue.admit("chat")
    waiting = queue.admit("chat")
    overflow = queue.admit("chat")
    repeated_overflow = queue.admit("chat")

    assert active.accepted
    assert waiting.accepted
    assert not overflow.accepted
    assert overflow.notify_overflow
    assert not repeated_overflow.accepted
    assert not repeated_overflow.notify_overflow
