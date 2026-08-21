"""Team-aware Yuque client lifecycle management."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from astrbot.api import logger

from .models import DEFAULT_TEAM_ID
from .yuque_client import YuqueClient


ClientFactory = Callable[[str, str], Any]


class TeamClientManager:
    """Lazy YuqueClient cache keyed by first-class team_id."""

    def __init__(
        self,
        team_registry,
        *,
        legacy_token: str = "",
        legacy_base_url: str = "https://www.yuque.com/api/v2",
        client_factory: ClientFactory = YuqueClient,
    ):
        self.team_registry = team_registry
        self.legacy_token = legacy_token
        self.legacy_base_url = legacy_base_url
        self.client_factory = client_factory
        self._clients: dict[str, Any] = {}

    def get(self, team_id: str = DEFAULT_TEAM_ID):
        team = self.team_registry.get(team_id or DEFAULT_TEAM_ID)
        if team is None:
            raise ValueError(f"unknown team_id: {team_id}")
        resolved_team_id = team.team_id or DEFAULT_TEAM_ID
        if resolved_team_id not in self._clients:
            token = team.yuque_token
            base_url = team.yuque_base_url
            if resolved_team_id == DEFAULT_TEAM_ID:
                token = token or self.legacy_token
                base_url = base_url or self.legacy_base_url
            self._clients[resolved_team_id] = self.client_factory(token, base_url)
        return self._clients[resolved_team_id]

    async def close_all(self) -> None:
        for team_id, client in list(self._clients.items()):
            close = getattr(client, "close", None)
            if not callable(close):
                continue
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.warning(f"[YuqueClient] 关闭团队客户端失败: team_id={team_id}, error={e}")
        self._clients.clear()

    @property
    def cached_team_ids(self) -> tuple[str, ...]:
        return tuple(self._clients.keys())
