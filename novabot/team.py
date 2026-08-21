"""Team registry for single-team compatibility and future multi-team sync."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from .models import DEFAULT_TEAM_ID, Team
from .yuque_client import YuqueClient


DEFAULT_YUQUE_BASE_URL = "https://www.yuque.com/api/v2"


_TEAM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_RESERVED_TEAM_IDS = {
    ".git",
    ".hg",
    ".svn",
    ".yuque-id-to-path.json",
    ".repos.json",
}


def is_safe_team_id(team_id: str) -> bool:
    """Return whether a configured team id is a single safe path component."""

    value = str(team_id or "").strip()
    if not value or value in {".", ".."} or ".." in value:
        return False
    if value.casefold() in _RESERVED_TEAM_IDS or value.startswith("."):
        return False
    if "/" in value or "\\" in value:
        return False
    return bool(_TEAM_ID_PATTERN.fullmatch(value))


def normalize_yuque_base_url(value: str | None, default: str = DEFAULT_YUQUE_BASE_URL) -> str:
    """Normalize Yuque API or web URLs to an API base URL."""

    raw = str(value or default or DEFAULT_YUQUE_BASE_URL).strip() or DEFAULT_YUQUE_BASE_URL
    base = raw.rstrip("/")
    if base.endswith("/api/v2"):
        return base
    if base.endswith("/api"):
        return f"{base}/v2"
    return f"{base}/api/v2"


@dataclass
class PendingTeamDiscovery:
    token: str
    yuque_base_url: str
    description: str = ""
    enabled: bool = True
    explicit_team_id: str = ""
    explicit_name: str = ""


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


def _team_entries_from_raw(raw: Any) -> list[Any]:
    """Normalize supported yuque_teams config shapes into entry items."""

    if not raw:
        return []
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError:
            return [stripped]
    if isinstance(raw, dict):
        raw = raw.get("teams", [])
    if isinstance(raw, list):
        return list(raw)
    return []


def _normalize_team_entry(item: Any) -> dict[str, Any] | None:
    """Normalize one yuque_teams item into a mapping."""

    if isinstance(item, str):
        stripped = item.strip()
        if not stripped:
            return None
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError:
                pass
        if isinstance(item, str):
            return {"yuque_token": stripped}
    if isinstance(item, dict):
        return item
    return None


class TeamRegistry:
    """Resolve team configuration without forcing a new config format."""

    def __init__(self, config: Any):
        self.config = config
        self._pending_discovery: list[PendingTeamDiscovery] = []
        self.discovery_errors: list[str] = []
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
        for raw_item in _team_entries_from_raw(raw):
            item = _normalize_team_entry(raw_item)
            if item is None:
                continue
            team_id = str(item.get("team_id") or item.get("id") or "").strip()
            token = str(item.get("yuque_token") or item.get("token") or "")
            name = str(item.get("name") or "").strip()
            base_url = normalize_yuque_base_url(
                item.get("yuque_base_url"),
                self._get("yuque_base_url", DEFAULT_YUQUE_BASE_URL),
            )
            enabled = _as_bool(item.get("enabled"), True)
            description = str(item.get("description") or "")
            if not team_id:
                if token:
                    self._pending_discovery.append(
                        PendingTeamDiscovery(
                            token=token,
                            yuque_base_url=base_url,
                            description=description,
                            enabled=enabled,
                            explicit_name=name,
                        )
                    )
                continue
            if not is_safe_team_id(team_id):
                logger.error(f"[TeamRegistry] 忽略非法 team_id: {team_id}")
                continue
            if token and not name:
                self._pending_discovery.append(
                    PendingTeamDiscovery(
                        token=token,
                        yuque_base_url=base_url,
                        description=description,
                        enabled=enabled,
                        explicit_team_id=team_id,
                    )
                )
            teams[team_id] = Team(
                team_id=team_id,
                name=name or team_id,
                description=description,
                yuque_token=token,
                yuque_base_url=base_url,
                enabled=enabled,
            )

        if DEFAULT_TEAM_ID not in teams:
            legacy_token = str(self._get("yuque_token", ""))
            teams[DEFAULT_TEAM_ID] = Team.default(
                yuque_token=legacy_token,
                yuque_base_url=normalize_yuque_base_url(
                    self._get("yuque_base_url", DEFAULT_YUQUE_BASE_URL)
                ),
                enabled=bool(legacy_token) or not has_explicit_teams,
            )
        return teams

    async def discover_pending(
        self,
        *,
        client_factory=YuqueClient,
    ) -> None:
        """Resolve token-only team entries into in-memory Team definitions."""

        if not self._pending_discovery:
            return

        pending = self._pending_discovery
        self._pending_discovery = []
        seen_ids = set(self._teams)
        for entry in pending:
            if not entry.enabled or not entry.token:
                continue
            client = client_factory(entry.token, entry.yuque_base_url)
            try:
                user = await client.get_user()
                user_id = user.get("id")
                user_type = str(user.get("type") or "").strip()
                if not user_id:
                    self.discovery_errors.append("缺少语雀用户/团队 ID，跳过自动团队发现")
                    continue
                discovered_team_id = (
                    f"group_{user_id}" if user_type == "Group" else f"user_{user_id}"
                )
                team_id = entry.explicit_team_id or discovered_team_id
                if not is_safe_team_id(team_id):
                    self.discovery_errors.append(f"自动发现 team_id 不安全: {team_id}")
                    continue
                if team_id in seen_ids and team_id != entry.explicit_team_id:
                    self.discovery_errors.append(f"重复的自动发现团队: team_id={team_id}")
                    continue
                existing = self._teams.get(team_id)
                name = (
                    entry.explicit_name
                    or str(user.get("name") or user.get("login") or "").strip()
                    or team_id
                )
                team = Team(
                    team_id=team_id,
                    name=name if not existing or existing.name == team_id else existing.name,
                    description=entry.description or (existing.description if existing else ""),
                    yuque_token=entry.token,
                    yuque_base_url=entry.yuque_base_url,
                    enabled=entry.enabled,
                )
                self._teams[team_id] = team
                seen_ids.add(team_id)
                logger.info(
                    "[TeamRegistry] 自动发现语雀团队: "
                    f"name={team.name}, team_id={team.team_id}, type={user_type or 'Unknown'}"
                )
            except Exception as e:
                self.discovery_errors.append(f"自动发现语雀团队失败: {type(e).__name__}: {e}")
                logger.warning(f"[TeamRegistry] 自动发现语雀团队失败: {e}")
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    result = close()
                    if hasattr(result, "__await__"):
                        await result

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

    @property
    def pending_discovery_count(self) -> int:
        return len(self._pending_discovery)

    def describe_for_agent(self) -> str:
        lines = []
        for team in self.list_enabled():
            desc = f"：{team.description}" if team.description else ""
            lines.append(f"- {team.name} (team_id={team.team_id}){desc}")
        return "\n".join(lines)
