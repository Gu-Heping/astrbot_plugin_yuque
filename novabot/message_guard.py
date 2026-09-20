"""Idempotency and per-conversation admission control for chat messages."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MessageIdentity:
    key: str
    display_id: str
    ttl_seconds: float
    source: str


@dataclass(frozen=True)
class DeduplicationResult:
    duplicate: bool
    identity: MessageIdentity


def _short_digest(value: str) -> str:
    return hashlib.blake2b(value.encode("utf-8"), digest_size=6).hexdigest()


def message_identity(event: Any, text: str) -> MessageIdentity:
    """Build a stable platform identity, with a short fallback for weak adapters."""
    platform_getter = getattr(event, "get_platform_id", None)
    platform = str(platform_getter() if callable(platform_getter) else "")
    if not platform:
        name_getter = getattr(event, "get_platform_name", None)
        platform = str(name_getter() if callable(name_getter) else "unknown")

    message_obj = getattr(event, "message_obj", None)
    message_id = str(getattr(message_obj, "message_id", "") or "").strip()
    if message_id and message_id.lower() != "unknown":
        raw_key = f"id:{platform}:{message_id}"
        return MessageIdentity(
            key=raw_key,
            display_id=_short_digest(raw_key),
            ttl_seconds=600.0,
            source="message_id",
        )

    sender_getter = getattr(event, "get_sender_id", None)
    sender = str(sender_getter() if callable(sender_getter) else "")
    umo = str(getattr(event, "unified_msg_origin", "") or "")
    raw_key = f"fallback:{platform}:{umo}:{sender}:{text}"
    digest = _short_digest(raw_key)
    return MessageIdentity(
        key=f"fallback:{digest}",
        display_id=digest,
        ttl_seconds=5.0,
        source="fallback",
    )


class MessageDeduplicator:
    """Bounded TTL cache used to suppress platform retries."""

    def __init__(self, max_entries: int = 4096):
        self.max_entries = max(1, int(max_entries))
        self._seen: OrderedDict[str, float] = OrderedDict()

    def check_and_mark(
        self,
        event: Any,
        text: str,
        *,
        now: float | None = None,
    ) -> DeduplicationResult:
        current = time.monotonic() if now is None else now
        identity = message_identity(event, text)
        self._prune(current)
        expires_at = self._seen.get(identity.key)
        if expires_at is not None and expires_at > current:
            self._seen.move_to_end(identity.key)
            return DeduplicationResult(True, identity)

        self._seen[identity.key] = current + identity.ttl_seconds
        self._seen.move_to_end(identity.key)
        while len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
        return DeduplicationResult(False, identity)

    def _prune(self, now: float) -> None:
        expired = [key for key, expiry in self._seen.items() if expiry <= now]
        for key in expired:
            self._seen.pop(key, None)


@dataclass
class _ConversationState:
    lock: asyncio.Lock
    admitted: int = 0
    overflow_notified: bool = False


class QueueAdmission:
    def __init__(
        self,
        coordinator: "ConversationQueue",
        key: str,
        state: _ConversationState,
        *,
        accepted: bool,
        notify_overflow: bool,
        depth: int,
    ):
        self.coordinator = coordinator
        self.key = key
        self.state = state
        self.accepted = accepted
        self.notify_overflow = notify_overflow
        self.depth = depth
        self._acquired = False

    async def __aenter__(self) -> "QueueAdmission":
        if self.accepted:
            await self.state.lock.acquire()
            self._acquired = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self.accepted:
            return
        if self._acquired:
            self.state.lock.release()
        self.state.admitted -= 1
        if self.state.admitted <= 0:
            current = self.coordinator._states.get(self.key)
            if current is self.state:
                self.coordinator._states.pop(self.key, None)


class ConversationQueue:
    """FIFO admission control for natural-language requests in one conversation."""

    def __init__(self, max_pending: int = 8):
        self.max_pending = max(0, int(max_pending))
        self._states: dict[str, _ConversationState] = {}

    def admit(self, key: str) -> QueueAdmission:
        state = self._states.setdefault(key, _ConversationState(asyncio.Lock()))
        waiting = max(0, state.admitted - 1)
        if state.admitted > 0 and waiting >= self.max_pending:
            notify = not state.overflow_notified
            state.overflow_notified = True
            return QueueAdmission(
                self,
                key,
                state,
                accepted=False,
                notify_overflow=notify,
                depth=waiting,
            )

        state.admitted += 1
        return QueueAdmission(
            self,
            key,
            state,
            accepted=True,
            notify_overflow=False,
            depth=max(0, state.admitted - 1),
        )

    @property
    def active_conversations(self) -> int:
        return len(self._states)
