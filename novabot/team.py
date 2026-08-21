"""Team registry for single-team compatibility and future multi-team sync."""

from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import logger

from .models import DEFAULT_TEAM_ID, Team


_TEAM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def is_safe_team_id(team_id: str) -> bool:
    """Return whether a configured team id is a single safe path component."""

    value = str(team_id or "").strip()
    if not value or value in {".", ".."} or ".." in value:
        return False
    if "/" in value or "\\" in value:
        return False
    return bool(_TEAM_ID_PATTERN.fullmatch(value))


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y", "on", "启用"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "禁用"}:
            return False
    return default


class TeamRegistry:
    """Resolve team configuration without forcing a new config format."""

    def __init__(self, config: Any):
        self.config = config
        self._teams = self._load_teams()

    def _get(self, key: str, default=None):
        getter = getattr(self.config, "get", None)
        if callable(getter):
            return getter(key, default)
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return default

    def _load_teams(self) -> dict[str, Team]:
        raw = self._get("yuque_teams", "")
        has_explicit_teams = bool(raw)
        teams: dict[str, Team] = {}
        if raw:
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = []
            if isinstance(raw, dict):
                raw = raw.get("teams", [])
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    team_id = str(item.get("team_id") or item.get("id") or "").strip()
                    if not team_id:
                        continue
                    if not is_safe_team_id(team_id):
                        logger.error(f"[TeamRegistry] 忽略非法 team_id: {team_id}")
                        continue
                    teams[team_id] = Team(
                        team_id=team_id,
                        name=str(item.get("name") or team_id),
                        description=str(item.get("description") or ""),
                        yuque_token=str(item.get("yuque_token") or item.get("token") or ""),
                        yuque_base_url=str(
                            item.get("yuque_base_url")
                            or self._get("yuque_base_url", "https://www.yuque.com/api/v2")
                        ),
                        enabled=_as_bool(item.get("enabled"), True),
                    )

        if DEFAULT_TEAM_ID not in teams:
            legacy_token = str(self._get("yuque_token", ""))
            teams[DEFAULT_TEAM_ID] = Team.default(
                yuque_token=legacy_token,
                yuque_base_url=str(self._get("yuque_base_url", "https://www.yuque.com/api/v2")),
                enabled=bool(legacy_token) or not has_explicit_teams,
            )
        return teams

    def get(self, team_id: str = DEFAULT_TEAM_ID) -> Team | None:
        if not team_id:
            return self._teams[DEFAULT_TEAM_ID]
        team = self._teams.get(team_id)
        if team:
            return team
        logger.error(f"[TeamRegistry] 未找到团队 {team_id}")
        return None

    def list_enabled(self) -> list[Team]:
        return [team for team in self._teams.values() if team.enabled]

    def describe_for_agent(self) -> str:
        lines = []
        for team in self.list_enabled():
            desc = f"：{team.description}" if team.description else ""
            lines.append(f"- {team.name} (team_id={team.team_id}){desc}")
        return "\n".join(lines)
