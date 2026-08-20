from __future__ import annotations

import pytest

from novabot.models import Team
from novabot.team_clients import TeamClientManager


class _Registry:
    def __init__(self, teams):
        self.teams = {team.team_id: team for team in teams}

    def get(self, team_id="default"):
        return self.teams.get(team_id) or self.teams["default"]


class _Client:
    def __init__(self, token, base_url):
        self.token = token
        self.base_url = base_url
        self.closed = False

    async def close(self):
        self.closed = True


def test_team_client_manager_uses_legacy_credentials_for_default_team():
    manager = TeamClientManager(
        _Registry([Team.default(yuque_token="", yuque_base_url="")]),
        legacy_token="legacy-token",
        legacy_base_url="https://legacy.example/api/v2",
        client_factory=_Client,
    )

    first = manager.get()
    second = manager.get("missing")

    assert first is second
    assert first.token == "legacy-token"
    assert first.base_url == "https://legacy.example/api/v2"
    assert manager.cached_team_ids == ("default",)


def test_team_client_manager_caches_non_default_team_client():
    manager = TeamClientManager(
        _Registry(
            [
                Team.default(yuque_token="legacy"),
                Team(team_id="other", name="Other", yuque_token="team-token", yuque_base_url="https://team.example/api/v2"),
            ]
        ),
        client_factory=_Client,
    )

    first = manager.get("other")
    second = manager.get("other")

    assert first is second
    assert first.token == "team-token"
    assert first.base_url == "https://team.example/api/v2"
    assert manager.cached_team_ids == ("other",)


@pytest.mark.asyncio
async def test_team_client_manager_closes_all_cached_clients():
    manager = TeamClientManager(
        _Registry(
            [
                Team.default(yuque_token="legacy"),
                Team(team_id="other", name="Other", yuque_token="team-token"),
            ]
        ),
        client_factory=_Client,
    )
    default_client = manager.get()
    other_client = manager.get("other")

    await manager.close_all()

    assert default_client.closed is True
    assert other_client.closed is True
    assert manager.cached_team_ids == ()
