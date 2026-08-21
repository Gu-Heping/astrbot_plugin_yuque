from __future__ import annotations

import json

import pytest

from novabot.doc_index import DocIndex
from novabot.models import Team
from novabot.sync import sync_all_repos, sync_repo_path_drift
from novabot.sync_coordinator import run_multi_team_sync


class _FakeYuqueClient:
    def __init__(self, *, user_id: int, repo_name: str, namespace: str, doc_id: int, title: str):
        self.user_id = user_id
        self.repo_name = repo_name
        self.namespace = namespace
        self.doc_id = doc_id
        self.title = title
        self.closed = False

    async def get_user(self):
        return {"type": "Group", "id": self.user_id}

    async def get_group_repos(self, user_id):
        assert user_id == self.user_id
        return [{"id": self.user_id * 10, "namespace": self.namespace, "name": self.repo_name}]

    async def get_user_repos(self, user_id):
        raise AssertionError("group sync should use get_group_repos")

    async def get_repo_toc(self, namespace):
        assert namespace == self.namespace
        return [
            {
                "type": "DOC",
                "id": self.doc_id,
                "uuid": f"uuid-{self.doc_id}",
                "title": self.title,
                "url": "intro",
            }
        ]

    async def get_doc_detail(self, namespace, slug):
        assert namespace == self.namespace
        assert slug == "intro"
        return {
            "id": self.doc_id,
            "title": self.title,
            "slug": slug,
            "body": f"# {self.title}\n\n来自 {self.repo_name} 的多团队同步测试。",
            "book": {"name": self.repo_name, "namespace": namespace},
            "user_id": 7,
            "user": {"id": 7, "name": "Alice"},
        }

    async def close(self):
        self.closed = True


class _FakeStorage:
    def __init__(self, docs_dir):
        self.docs_dir = docs_dir
        self.data_dir = docs_dir.parent
        self.state = {}
        self.saved_states = []
        self.progress_calls = []

    def load_sync_state(self):
        return dict(self.state)

    def save_sync_state(self, state):
        self.state = dict(state)
        self.saved_states.append(dict(state))

    def update_progress(self, current, total, repo_name):
        self.progress_calls.append((current, total, repo_name))
        state = self.load_sync_state()
        state["progress"] = {
            "current": current,
            "total": total,
            "current_repo": repo_name,
        }
        self.save_sync_state(state)


class _FailingClient:
    async def get_user(self):
        raise RuntimeError("boom")

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_sync_all_repos_can_append_isolated_team_indexes(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    members = {"7": {"name": "Alice"}}

    default_result = await sync_all_repos(
        client=_FakeYuqueClient(
            user_id=1,
            repo_name="工程",
            namespace="nova/eng",
            doc_id=101,
            title="默认团队文档",
        ),
        output_dir=docs_dir,
        members=members,
        team=Team.default(yuque_token="legacy"),
        replace_index=False,
        write_repo_cache=False,
    )
    other_team = Team(
        team_id="other",
        name="Other",
        yuque_token="team-token",
        yuque_base_url="https://www.yuque.com/api/v2",
    )
    other_result = await sync_all_repos(
        client=_FakeYuqueClient(
            user_id=2,
            repo_name="工程",
            namespace="other/eng",
            doc_id=101,
            title="其他团队文档",
        ),
        output_dir=docs_dir,
        members=members,
        team=other_team,
        team_path_prefix="other",
        replace_index=False,
        write_repo_cache=False,
    )

    index = DocIndex(str(tmp_path / "doc_index.db"))
    default_docs = index.search(team_id="default")
    other_docs = index.search(team_id="other")

    assert default_result["docs"] == 1
    assert other_result["docs"] == 1
    assert default_docs[0]["file_path"] == "工程/默认团队文档.md"
    assert other_docs[0]["file_path"] == "other/工程/其他团队文档.md"
    assert (docs_dir / "工程" / "默认团队文档.md").exists()
    assert (docs_dir / "other" / "工程" / "其他团队文档.md").exists()

    repos_info = default_result["repos_info"] + other_result["repos_info"]
    (docs_dir / ".repos.json").write_text(
        json.dumps(repos_info, ensure_ascii=False),
        encoding="utf-8",
    )
    cached = json.loads((docs_dir / ".repos.json").read_text(encoding="utf-8"))
    assert {repo["team_id"] for repo in cached} == {"default", "other"}
    global_index = json.loads((docs_dir / ".yuque-id-to-path.json").read_text(encoding="utf-8"))
    assert global_index["101"] == "工程/默认团队文档.md"
    assert global_index["other:101"] == "other/工程/其他团队文档.md"


@pytest.mark.asyncio
async def test_multi_team_sync_coordinator_records_state_and_cache(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    storage = _FakeStorage(docs_dir)
    teams = [
        Team.default(yuque_token="legacy"),
        Team(team_id="other", name="Other", yuque_token="team-token"),
    ]
    clients = {
        "default": _FakeYuqueClient(
            user_id=1,
            repo_name="工程",
            namespace="nova/eng",
            doc_id=101,
            title="默认团队文档",
        ),
        "other": _FakeYuqueClient(
            user_id=2,
            repo_name="工程",
            namespace="other/eng",
            doc_id=202,
            title="其他团队文档",
        ),
    }

    summary = await run_multi_team_sync(
        teams=teams,
        storage=storage,
        docs_dir=docs_dir,
        members={"7": {"name": "Alice"}},
        client_factory=lambda team: clients[team.team_id],
    )

    assert summary["teams_count"] == 2
    assert summary["result"]["docs"] == 2
    assert storage.state["in_progress"] is False
    assert storage.state["docs_count"] == 2
    assert storage.state["teams"]["default"]["docs_count"] == 1
    assert storage.state["teams"]["other"]["docs_count"] == 1
    assert any(state.get("team_progress", {}).get("team_id") == "other" for state in storage.saved_states)
    assert storage.progress_calls == [(1, 1, "工程"), (1, 1, "工程")]
    assert clients["default"].closed is True
    assert clients["other"].closed is True

    repos = json.loads((docs_dir / ".repos.json").read_text(encoding="utf-8"))
    repos_cache = json.loads((tmp_path / "yuque_repos.json").read_text(encoding="utf-8"))
    assert {repo["team_id"] for repo in repos} == {"default", "other"}
    assert repos_cache == repos


@pytest.mark.asyncio
async def test_multi_team_sync_preserves_failed_team_repos_cache(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    docs_dir.mkdir()
    storage = _FakeStorage(docs_dir)
    (docs_dir / ".repos.json").write_text(
        json.dumps(
            [
                {
                    "id": 20,
                    "name": "工程",
                    "namespace": "other/eng",
                    "team_id": "other",
                    "team_name": "Other",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    teams = [
        Team.default(yuque_token="legacy"),
        Team(team_id="other", name="Other", yuque_token="team-token"),
    ]
    clients = {
        "default": _FakeYuqueClient(
            user_id=1,
            repo_name="工程",
            namespace="nova/eng",
            doc_id=101,
            title="默认团队文档",
        ),
        "other": _FailingClient(),
    }

    summary = await run_multi_team_sync(
        teams=teams,
        storage=storage,
        docs_dir=docs_dir,
        members={"7": {"name": "Alice"}},
        client_factory=lambda team: clients[team.team_id],
    )

    repos = json.loads((docs_dir / ".repos.json").read_text(encoding="utf-8"))
    assert summary["team_states"]["other"]["errors_count"] == 1
    assert {repo["team_id"] for repo in repos} == {"default", "other"}
    assert any(repo["namespace"] == "other/eng" for repo in repos)


@pytest.mark.asyncio
async def test_targeted_team_sync_preserves_non_selected_repo_cache(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    docs_dir.mkdir()
    storage = _FakeStorage(docs_dir)
    (docs_dir / ".repos.json").write_text(
        json.dumps(
            [
                {
                    "id": 20,
                    "name": "工程",
                    "namespace": "other/eng",
                    "team_id": "other",
                    "team_name": "Other",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = await run_multi_team_sync(
        teams=[Team.default(yuque_token="legacy")],
        storage=storage,
        docs_dir=docs_dir,
        members={"7": {"name": "Alice"}},
        client_factory=lambda team: _FakeYuqueClient(
            user_id=1,
            repo_name="默认工程",
            namespace="nova/eng",
            doc_id=101,
            title="默认团队文档",
        ),
    )

    repos = json.loads((docs_dir / ".repos.json").read_text(encoding="utf-8"))
    assert summary["teams_count"] == 1
    assert {repo["team_id"] for repo in repos} == {"default", "other"}
    assert any(repo["namespace"] == "other/eng" for repo in repos)


@pytest.mark.asyncio
async def test_targeted_default_sync_preserves_other_team_doc_index(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    index = DocIndex(str(tmp_path / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 202,
            "title": "其他团队文档",
            "team_id": "other",
            "team_name": "Other",
            "book_name": "工程",
            "file_path": "other/工程/其他团队文档.md",
        }
    )
    storage = _FakeStorage(docs_dir)

    await run_multi_team_sync(
        teams=[Team.default(yuque_token="legacy")],
        storage=storage,
        docs_dir=docs_dir,
        members={"7": {"name": "Alice"}},
        client_factory=lambda team: _FakeYuqueClient(
            user_id=1,
            repo_name="默认工程",
            namespace="nova/eng",
            doc_id=101,
            title="默认团队文档",
        ),
    )

    refreshed = DocIndex(str(tmp_path / "doc_index.db"))
    assert [doc["title"] for doc in refreshed.search(team_id="default")] == ["默认团队文档"]
    assert [doc["title"] for doc in refreshed.search(team_id="other")] == ["其他团队文档"]


def test_path_drift_uses_team_scoped_document_id(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    default_repo = docs_dir / "工程"
    other_repo = docs_dir / "other" / "工程"
    default_repo.mkdir(parents=True)
    other_repo.mkdir(parents=True)
    (default_repo / "旧标题.md").write_text("默认团队旧文件", encoding="utf-8")
    (other_repo / "旧标题.md").write_text("其他团队旧文件", encoding="utf-8")
    index = {
        "42": "工程/旧标题.md",
        "other:42": "other/工程/旧标题.md",
    }
    toc = [
        {
            "type": "DOC",
            "id": 42,
            "uuid": "doc-42",
            "title": "新标题",
            "url": "new-title",
        }
    ]

    moves = sync_repo_path_drift(docs_dir, "other/工程", toc, index, team_id="other")

    assert moves == [("other/工程/旧标题.md", "other/工程/新标题.md")]
    assert index["42"] == "工程/旧标题.md"
    assert index["other:42"] == "other/工程/新标题.md"
    assert (default_repo / "旧标题.md").exists()


@pytest.mark.asyncio
async def test_default_team_orphan_cleanup_preserves_other_team_root(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    other_repo = docs_dir / "other" / "工程"
    other_repo.mkdir(parents=True)
    (other_repo / "其他团队文档.md").write_text("其他团队内容", encoding="utf-8")

    result = await sync_all_repos(
        client=_FakeYuqueClient(
            user_id=1,
            repo_name="工程",
            namespace="nova/eng",
            doc_id=101,
            title="默认团队文档",
        ),
        output_dir=docs_dir,
        members={"7": {"name": "Alice"}},
        team=Team.default(yuque_token="legacy"),
        replace_index=False,
        cleanup_orphans=True,
        write_repo_cache=False,
        protected_root_dirs={"other"},
    )

    assert result["docs"] == 1
    assert (docs_dir / "工程" / "默认团队文档.md").exists()
    assert (other_repo / "其他团队文档.md").exists()


@pytest.mark.asyncio
async def test_orphan_repo_cleanup_removes_only_matching_global_index_entries(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    old_repo = docs_dir / "旧工程"
    other_repo = docs_dir / "other" / "工程"
    old_repo.mkdir(parents=True)
    other_repo.mkdir(parents=True)
    (old_repo / "旧文档.md").write_text("旧默认团队内容", encoding="utf-8")
    (other_repo / "其他团队文档.md").write_text("其他团队内容", encoding="utf-8")
    (docs_dir / ".yuque-id-to-path.json").write_text(
        json.dumps(
            {
                "99": "旧工程/旧文档.md",
                "other:99": "other/工程/其他团队文档.md",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    await sync_all_repos(
        client=_FakeYuqueClient(
            user_id=1,
            repo_name="工程",
            namespace="nova/eng",
            doc_id=101,
            title="默认团队文档",
        ),
        output_dir=docs_dir,
        members={"7": {"name": "Alice"}},
        team=Team.default(yuque_token="legacy"),
        replace_index=False,
        cleanup_orphans=True,
        write_repo_cache=False,
        protected_root_dirs={"other"},
    )

    index = json.loads((docs_dir / ".yuque-id-to-path.json").read_text(encoding="utf-8"))
    assert "99" not in index
    assert index["other:99"] == "other/工程/其他团队文档.md"
    assert index["101"] == "工程/默认团队文档.md"
    assert not old_repo.exists()
    assert (other_repo / "其他团队文档.md").exists()


@pytest.mark.asyncio
async def test_sync_rebuilds_only_current_team_doc_index(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    index = DocIndex(str(tmp_path / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 99,
            "title": "默认团队旧知识库文档",
            "team_id": "default",
            "team_name": "NOVA",
            "book_name": "旧工程",
            "file_path": "旧工程/旧文档.md",
        }
    )
    index.add_doc(
        {
            "yuque_id": 99,
            "title": "其他团队文档",
            "team_id": "other",
            "team_name": "Other",
            "book_name": "工程",
            "file_path": "other/工程/其他团队文档.md",
        }
    )

    await sync_all_repos(
        client=_FakeYuqueClient(
            user_id=1,
            repo_name="工程",
            namespace="nova/eng",
            doc_id=101,
            title="默认团队文档",
        ),
        output_dir=docs_dir,
        members={"7": {"name": "Alice"}},
        team=Team.default(yuque_token="legacy"),
        replace_index=False,
        cleanup_orphans=False,
        write_repo_cache=False,
    )

    refreshed = DocIndex(str(tmp_path / "doc_index.db"))
    assert [doc["title"] for doc in refreshed.search(team_id="default")] == ["默认团队文档"]
    assert [doc["title"] for doc in refreshed.search(team_id="other")] == ["其他团队文档"]
