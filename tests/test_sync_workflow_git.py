from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from novabot.models import Team
from novabot.sync_workflow import (
    build_sync_commit_message,
    commit_sync_changes,
    mark_sync_failed,
    refresh_collaboration_artifacts,
    run_background_sync_pipeline,
    select_member_sync_team,
    select_sync_teams,
    sync_team_members,
)


class _FakeGit:
    instances = []

    def __init__(self, repo_dir, *, is_repo=True, has_identity=True):
        self.repo_dir = repo_dir
        self.is_repo = is_repo
        self.has_identity = has_identity
        self.commits = []
        _FakeGit.instances.append(self)

    def is_git_repo(self):
        return self.is_repo

    def has_user_identity(self):
        return self.has_identity

    def add_commit(self, files, message):
        self.commits.append((files, message))
        return "abc1234"


@dataclass
class _Storage:
    state: dict

    def load_sync_state(self):
        return dict(self.state)

    def save_sync_state(self, state):
        self.state = dict(state)

    def load_members(self):
        return {"1": {"name": "Alice"}}

    def save_members(self, members):
        self.members = dict(members)


class _Registry:
    def __init__(self, teams):
        self.teams = teams

    def list_enabled(self):
        return [team for team in self.teams if team.enabled]


class _MemberClient:
    def __init__(self, user_info, members=None):
        self.user_info = user_info
        self.members = members or []

    async def get_user(self):
        return self.user_info

    async def get_group_members(self, group_id):
        self.group_id = group_id
        return self.members


class _CollaborationManager:
    def __init__(self, stats=None):
        self.stats = stats or {"total_collaborations": 2, "total_members": 3}

    def get_network_stats(self):
        return self.stats


class _TrajectoryManager:
    def __init__(self, members=None):
        self.members = members or ["1", "2"]

    def get_all_active_members(self, days=30):
        self.days = days
        return self.members


def test_commit_sync_changes_commits_porcelain_files(tmp_path):
    _FakeGit.instances = []

    result = commit_sync_changes(
        docs_dir=tmp_path,
        result={"docs": 3, "removed": 2},
        git_factory=_FakeGit,
        status_runner=lambda *args, **kwargs: SimpleNamespace(stdout=" M a.md\n?? b.md\n"),
    )

    assert result == "abc1234"
    assert _FakeGit.instances[0].commits == [
        (["a.md", "b.md"], "sync: 同步 3 篇文档, 清理 2 个文件")
    ]


def test_commit_sync_changes_skips_when_disabled_or_clean(tmp_path):
    _FakeGit.instances = []

    disabled = commit_sync_changes(
        docs_dir=tmp_path,
        result={"docs": 3},
        enabled=False,
        git_factory=_FakeGit,
    )
    clean = commit_sync_changes(
        docs_dir=tmp_path,
        result={"docs": 3},
        git_factory=_FakeGit,
        status_runner=lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )

    assert disabled is None
    assert clean is None
    assert len(_FakeGit.instances) == 1
    assert _FakeGit.instances[0].commits == []


def test_build_sync_commit_message_omits_removed_suffix_when_zero():
    assert build_sync_commit_message({"docs": 4, "removed": 0}) == "sync: 同步 4 篇文档"


def test_mark_sync_failed_clears_volatile_progress_fields():
    storage = _Storage(
        {
            "in_progress": True,
            "progress": {"current": 1},
            "team_progress": {"team_id": "other"},
            "status": "chunk_indexing",
            "chunk_progress": {"current": 1},
            "rag_progress": {"current": 1},
            "last_sync": "2026-01-01T00:00:00+00:00",
        }
    )

    mark_sync_failed(storage)

    assert storage.state["in_progress"] is False
    assert storage.state["progress"] is None
    assert storage.state["team_progress"] is None
    assert "status" not in storage.state
    assert "chunk_progress" not in storage.state
    assert "rag_progress" not in storage.state
    assert storage.state["last_sync"] == "2026-01-01T00:00:00+00:00"


def test_select_member_sync_team_prefers_default_when_available():
    team, error = select_member_sync_team(
        [
            Team.default(yuque_token="legacy"),
            Team(team_id="other", name="Other", yuque_token="team-token"),
        ]
    )

    assert error is None
    assert team.team_id == "default"


def test_select_member_sync_team_uses_first_syncable_non_default_when_default_disabled():
    team, error = select_member_sync_team(
        [
            Team.default(yuque_token="", enabled=False),
            Team(team_id="other", name="Other", yuque_token="team-token"),
        ]
    )

    assert error is None
    assert team.team_id == "other"


def test_select_member_sync_team_accepts_explicit_team_id():
    team, error = select_member_sync_team(
        [
            Team.default(yuque_token="legacy"),
            Team(team_id="other", name="Other", yuque_token="team-token"),
        ],
        requested_team_id="other",
    )

    assert error is None
    assert team.team_id == "other"


def test_select_member_sync_team_reports_missing_or_unsyncable_team():
    missing_team, missing_error = select_member_sync_team(
        [Team(team_id="other", name="Other", yuque_token="team-token")],
        requested_team_id="missing",
    )
    no_token_team, no_token_error = select_member_sync_team(
        [Team.default(yuque_token="")],
    )

    assert missing_team is None
    assert missing_error == "❌ 未找到可同步团队: missing"
    assert no_token_team is None
    assert no_token_error == "❌ 未配置语雀 Token"


def test_select_sync_teams_can_select_all_or_one_team():
    teams = [
        Team.default(yuque_token="legacy"),
        Team(team_id="other", name="Other", yuque_token="team-token"),
        Team(team_id="off", name="Off", yuque_token="off-token", enabled=False),
    ]

    selected, error = select_sync_teams(teams)
    one, one_error = select_sync_teams(teams, requested_team_id="other")
    missing, missing_error = select_sync_teams(teams, requested_team_id="missing")

    assert [team.team_id for team in selected] == ["default", "other"]
    assert error is None
    assert [team.team_id for team in one] == ["other"]
    assert one_error is None
    assert missing == []
    assert missing_error == "❌ 未找到可同步团队: missing"


@pytest.mark.asyncio
async def test_sync_team_members_saves_group_members():
    storage = _Storage({})
    client = _MemberClient(
        {"type": "Group", "id": 7},
        [
            {"user": {"id": 1, "name": "Alice", "login": "alice"}},
            {"user_id": 2, "user": {"name": "Bob", "login": "bob"}},
            {"user": {"name": "No Id"}},
        ],
    )

    text = await sync_team_members(client=client, storage=storage)

    assert "共 2 人" in text
    assert storage.members == {
        "1": {"name": "Alice", "login": "alice"},
        "2": {"name": "Bob", "login": "bob"},
    }


@pytest.mark.asyncio
async def test_sync_team_members_skips_non_group_or_empty_members():
    non_group = await sync_team_members(
        client=_MemberClient({"type": "User"}),
        storage=_Storage({}),
    )
    empty = await sync_team_members(
        client=_MemberClient({"type": "Group", "id": 7}, []),
        storage=_Storage({}),
    )

    assert "非团队 Token" in non_group
    assert "未获取到成员" in empty


@pytest.mark.asyncio
async def test_run_background_sync_pipeline_orchestrates_post_sync_and_commit(tmp_path):
    storage = _Storage({})
    calls = {}

    async def sync_runner(**kwargs):
        calls["sync"] = kwargs
        return {
            "teams_count": 1,
            "result": {"docs": 3, "removed": 1, "errors": 0},
            "team_states": {},
            "repos_info": [],
        }

    async def post_sync_runner(**kwargs):
        calls["post"] = kwargs

    def commit_runner(**kwargs):
        calls["commit"] = kwargs
        return "abc1234"

    summary = await run_background_sync_pipeline(
        team_registry=_Registry(
            [
                Team.default(yuque_token=""),
                Team(team_id="other", name="Other", yuque_token="team-token"),
            ]
        ),
        storage=storage,
        docs_dir=tmp_path,
        rag="rag",
        collaboration_manager="collab",
        trajectory_manager="trajectory",
        update_collaboration=lambda: None,
        init_trajectories=lambda: None,
        chunk_store="chunks",
        yuque_base_url="https://yuque.example/api/v2",
        chunk_size=900,
        chunk_overlap=90,
        git_enabled=True,
        sync_runner=sync_runner,
        post_sync_runner=post_sync_runner,
        commit_runner=commit_runner,
    )

    assert summary["result"]["docs"] == 3
    assert [team.team_id for team in calls["sync"]["teams"]] == ["other"]
    assert calls["sync"]["members"] == {"1": {"name": "Alice"}}
    assert calls["post"]["chunk_size"] == 900
    assert calls["post"]["chunk_overlap"] == 90
    assert calls["commit"]["enabled"] is True
    assert calls["commit"]["result"] == {"docs": 3, "removed": 1, "errors": 0}


@pytest.mark.asyncio
async def test_run_background_sync_pipeline_accepts_requested_team_id(tmp_path):
    storage = _Storage({})
    calls = {}

    async def sync_runner(**kwargs):
        calls["sync"] = kwargs
        return {
            "teams_count": 1,
            "result": {"docs": 1, "removed": 0, "errors": 0},
            "team_states": {},
            "repos_info": [],
        }

    async def post_sync_runner(**kwargs):
        calls["post"] = kwargs

    summary = await run_background_sync_pipeline(
        team_registry=_Registry(
            [
                Team.default(yuque_token="legacy"),
                Team(team_id="other", name="Other", yuque_token="team-token"),
            ]
        ),
        storage=storage,
        docs_dir=tmp_path,
        rag=None,
        collaboration_manager=None,
        trajectory_manager=None,
        update_collaboration=lambda: None,
        init_trajectories=lambda: None,
        requested_team_id="other",
        sync_runner=sync_runner,
        post_sync_runner=post_sync_runner,
        commit_runner=lambda **kwargs: None,
    )

    assert summary["teams_count"] == 1
    assert [team.team_id for team in calls["sync"]["teams"]] == ["other"]
    assert calls["post"]["result"] == {"docs": 1, "removed": 0, "errors": 0}


def test_refresh_collaboration_artifacts_formats_success_message():
    called = []

    text = refresh_collaboration_artifacts(
        collaboration_manager=_CollaborationManager(),
        trajectory_manager=_TrajectoryManager(),
        update_collaboration=lambda: called.append("collab"),
        init_trajectories=lambda: called.append("trajectory"),
    )

    assert called == ["collab", "trajectory"]
    assert "协作关系: 2 条" in text
    assert "参与成员: 3 人" in text
    assert "成员轨迹: 2 人有活动记录" in text


def test_refresh_collaboration_artifacts_handles_missing_systems():
    text = refresh_collaboration_artifacts(
        collaboration_manager=None,
        trajectory_manager=None,
        update_collaboration=lambda: None,
        init_trajectories=lambda: None,
    )

    assert text == "❌ 数据系统未初始化"


def test_refresh_collaboration_artifacts_reports_partial_failures():
    def fail():
        raise RuntimeError("boom")

    text = refresh_collaboration_artifacts(
        collaboration_manager=_CollaborationManager(),
        trajectory_manager=_TrajectoryManager(["1"]),
        update_collaboration=fail,
        init_trajectories=lambda: None,
    )

    assert "协作网络更新失败: boom" in text
    assert "成员轨迹: 1 人有活动记录" in text
