"""Multi-team Yuque sync orchestration.

This module owns the domain flow for running enabled teams through the syncer,
recording team-scoped state, and publishing the aggregated repository cache.
The AstrBot plugin entrypoint should only schedule it and run app-level
post-processing such as RAG rebuilds or optional Git commits.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .models import DEFAULT_TEAM_ID, Team
from .sync import sync_all_repos
from .yuque_client import YuqueClient


ClientFactory = Callable[[Team], Any]


def sync_team_path_prefix(team_id: str) -> str:
    """Return the docs subdirectory used to isolate non-default teams."""

    return "" if team_id == DEFAULT_TEAM_ID else team_id


def syncable_teams(teams: list[Team]) -> list[Team]:
    """Filter enabled teams down to those with credentials."""

    return [team for team in teams if team.enabled and team.yuque_token]


async def run_multi_team_sync(
    *,
    teams: list[Team],
    storage,
    docs_dir: Path,
    members: dict[str, dict] | None = None,
    client_factory: ClientFactory | None = None,
) -> dict:
    """Synchronize all configured teams with isolated lifecycle state."""

    selected_teams = syncable_teams(teams)
    if not selected_teams:
        logger.error("[Sync] 未配置可同步的语雀团队")
        return _empty_sync_result()

    _save_start_state(storage, selected_teams)
    client_factory = client_factory or _default_client_factory
    members = members or {}

    all_repos_info: list[dict] = []
    team_states: dict[str, dict] = {}
    total_result = {
        "repos_count": 0,
        "docs": 0,
        "titles": 0,
        "errors": 0,
        "removed": 0,
        "token_type": "多团队" if len(selected_teams) > 1 else "未知",
    }

    for team_index, team in enumerate(selected_teams, 1):
        _save_team_progress(storage, team, team_index, len(selected_teams))

        def team_progress(current: int, total: int, repo_name: str) -> None:
            storage.update_progress(current, total, repo_name)
            _save_team_progress(storage, team, team_index, len(selected_teams))

        client = client_factory(team)
        try:
            result = await sync_all_repos(
                client=client,
                output_dir=docs_dir,
                members=members,
                progress_callback=team_progress,
                team=team,
                team_path_prefix=sync_team_path_prefix(team.team_id),
                replace_index=len(selected_teams) == 1 and team.team_id == DEFAULT_TEAM_ID,
                cleanup_orphans=True,
                write_repo_cache=False,
                protected_root_dirs={
                    other.team_id for other in selected_teams if other.team_id != DEFAULT_TEAM_ID
                },
            )
        except Exception as e:
            logger.error(f"[Sync] 团队同步失败: {team.name} ({team.team_id}) - {e}", exc_info=True)
            result = _failed_team_result(team)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                await close()

        all_repos_info.extend(result.get("repos_info", []))
        team_states[team.team_id] = _team_state(team, result)
        for key in ("repos_count", "docs", "titles", "errors", "removed"):
            total_result[key] += result.get(key, 0)

    write_repos_cache(docs_dir, all_repos_info)
    _save_finish_state(storage, total_result, team_states)

    return {
        "teams_count": len(selected_teams),
        "result": total_result,
        "team_states": team_states,
        "repos_info": all_repos_info,
    }


def write_repos_cache(docs_dir: Path, repos_info: list[dict]) -> None:
    """Write aggregated repository metadata used by tools and chunk indexer."""

    repos_file = docs_dir / ".repos.json"
    repos_file.write_text(json.dumps(repos_info, ensure_ascii=False, indent=2), encoding="utf-8")
    repos_cache = docs_dir.parent / "yuque_repos.json"
    repos_cache.write_text(json.dumps(repos_info, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_client_factory(team: Team) -> YuqueClient:
    return YuqueClient(team.yuque_token, team.yuque_base_url)


def _save_start_state(storage, teams: list[Team]) -> None:
    state = storage.load_sync_state()
    state["in_progress"] = True
    state["team_progress"] = {
        "current": 0,
        "total": len(teams),
        "team_id": "",
        "team_name": "",
    }
    storage.save_sync_state(state)


def _save_team_progress(storage, team: Team, current: int, total: int) -> None:
    state = storage.load_sync_state()
    state["team_progress"] = {
        "current": current,
        "total": total,
        "team_id": team.team_id,
        "team_name": team.name,
    }
    storage.save_sync_state(state)


def _save_finish_state(storage, total_result: dict, team_states: dict[str, dict]) -> None:
    state = {
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "repos_count": total_result.get("repos_count", 0),
        "docs_count": total_result.get("docs", 0),
        "token_type": total_result.get("token_type", "未知"),
        "teams": team_states,
        "in_progress": False,
        "progress": None,
        "team_progress": None,
    }
    storage.save_sync_state(state)


def _team_state(team: Team, result: dict) -> dict:
    return {
        "team_name": team.name,
        "repos_count": result.get("repos_count", 0),
        "docs_count": result.get("docs", 0),
        "titles_count": result.get("titles", 0),
        "errors_count": result.get("errors", 0),
        "removed_count": result.get("removed", 0),
        "token_type": result.get("token_type", "未知"),
    }


def _failed_team_result(team: Team) -> dict:
    return {
        "team_id": team.team_id,
        "team_name": team.name,
        "repos_count": 0,
        "docs": 0,
        "titles": 0,
        "errors": 1,
        "removed": 0,
        "token_type": "未知",
        "repos_info": [],
    }


def _empty_sync_result() -> dict:
    return {
        "teams_count": 0,
        "result": {
            "repos_count": 0,
            "docs": 0,
            "titles": 0,
            "errors": 1,
            "removed": 0,
            "token_type": "未知",
        },
        "team_states": {},
        "repos_info": [],
    }
