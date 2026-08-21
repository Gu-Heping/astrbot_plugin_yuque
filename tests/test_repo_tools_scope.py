from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from novabot.doc_index import DocIndex
from novabot.models import Team
from novabot.tools.repo import ListKnowledgeBasesTool, ListRepoDocsTool, ListTeamsTool


@dataclass
class _Storage:
    docs_dir: object
    data_dir: object
    state: dict | None = None

    def load_sync_state(self):
        return dict(self.state or {})


class _Plugin:
    def __init__(self, docs_dir, data_dir, *, team_registry=None, state=None):
        self.storage = _Storage(docs_dir=docs_dir, data_dir=data_dir, state=state)
        self.team_registry = team_registry


class _TeamRegistry:
    def __init__(self, teams):
        self.teams = teams

    def list_enabled(self):
        return [team for team in self.teams if team.enabled]


def _write_repo_cache(docs_dir, data_dir):
    repos = [
        {
            "team_id": "default",
            "team_name": "NOVA",
            "name": "工程",
            "namespace": "nova/eng",
            "items_count": 1,
            "description": "默认团队工程知识库",
        },
        {
            "team_id": "other",
            "team_name": "Other",
            "name": "工程",
            "namespace": "other/eng",
            "items_count": 1,
            "description": "其他团队工程知识库",
        },
    ]
    (docs_dir / ".repos.json").write_text(json.dumps(repos, ensure_ascii=False), encoding="utf-8")
    (data_dir / "yuque_repos.json").write_text(json.dumps(repos, ensure_ascii=False), encoding="utf-8")


def _write_repo_docs(docs_dir):
    default_repo = docs_dir / "工程"
    other_repo = docs_dir / "other" / "工程"
    default_repo.mkdir(parents=True)
    other_repo.mkdir(parents=True)
    toc = [{"type": "DOC", "uuid": "u1", "title": "部署", "slug": "deploy", "url": "deploy"}]
    (default_repo / ".toc.json").write_text(json.dumps(toc, ensure_ascii=False), encoding="utf-8")
    (other_repo / ".toc.json").write_text(json.dumps(toc, ensure_ascii=False), encoding="utf-8")


def _seed_doc_index(data_dir):
    index = DocIndex(str(data_dir / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 1,
            "title": "部署",
            "slug": "deploy",
            "author": "Alice",
            "team_id": "default",
            "team_name": "NOVA",
            "book_name": "工程",
            "file_path": "工程/部署.md",
        }
    )
    index.add_doc(
        {
            "yuque_id": 1,
            "title": "部署",
            "slug": "deploy",
            "author": "Bob",
            "team_id": "other",
            "team_name": "Other",
            "book_name": "工程",
            "file_path": "other/工程/部署.md",
        }
    )


@pytest.mark.asyncio
async def test_list_knowledge_bases_exposes_team_scope(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_repo_cache(docs_dir, data_dir)

    tool = ListKnowledgeBasesTool(plugin=_Plugin(docs_dir, data_dir))

    text = await tool.run(None)

    assert "团队: NOVA (team_id=default)" in text
    assert "团队: Other (team_id=other)" in text
    assert "scope: team_id=default, repository=工程" in text
    assert "scope: team_id=other, repository=工程" in text


@pytest.mark.asyncio
async def test_list_teams_exposes_descriptions_sync_state_and_next_scope(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_repo_cache(docs_dir, data_dir)
    registry = _TeamRegistry(
        [
            Team.default(yuque_token="legacy"),
            Team(team_id="other", name="Other", description="其他团队文档", yuque_token="token"),
        ]
    )
    state = {
        "teams": {
            "default": {"repos_count": 1, "docs_count": 8},
            "other": {"repos_count": 1, "docs_count": 2},
        }
    }
    tool = ListTeamsTool(plugin=_Plugin(docs_dir, data_dir, team_registry=registry, state=state))

    text = await tool.run(None)

    assert "NOVA (team_id=default, enabled)" in text
    assert "Other (team_id=other, enabled)" in text
    assert "描述: 其他团队文档" in text
    assert "同步: 1 个知识库, 2 篇文档" in text
    assert "scope: team_id=other" in text
    assert "list_knowledge_bases(team_id=...)" in text


@pytest.mark.asyncio
async def test_list_teams_can_infer_from_repo_cache_without_registry(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_repo_cache(docs_dir, data_dir)
    tool = ListTeamsTool(plugin=_Plugin(docs_dir, data_dir))

    text = await tool.run(None)

    assert "NOVA (team_id=default, enabled)" in text
    assert "Other (team_id=other, enabled)" in text


@pytest.mark.asyncio
async def test_list_knowledge_bases_can_filter_team(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_repo_cache(docs_dir, data_dir)

    tool = ListKnowledgeBasesTool(plugin=_Plugin(docs_dir, data_dir))

    text = await tool.run(None, team_id="other")

    assert "团队: Other (team_id=other)" in text
    assert "团队: NOVA" not in text


@pytest.mark.asyncio
async def test_list_knowledge_bases_filesystem_fallback_keeps_team_roots_separate(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_repo_docs(docs_dir)

    tool = ListKnowledgeBasesTool(plugin=_Plugin(docs_dir, data_dir))

    text = await tool.run(None)

    assert "• 工程" in text
    assert "scope: team_id=default, path_prefix=工程" in text
    assert "scope: team_id=other, path_prefix=other/工程" in text
    assert "• other" not in text


@pytest.mark.asyncio
async def test_list_repo_docs_uses_team_id_for_same_named_repo(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_repo_cache(docs_dir, data_dir)
    _write_repo_docs(docs_dir)
    _seed_doc_index(data_dir)

    tool = ListRepoDocsTool(plugin=_Plugin(docs_dir, data_dir))

    text = await tool.run(None, repo_name="工程", team_id="other")

    assert "scope: team_id=other, repository=工程" in text
    assert "path=other/工程/部署.md" in text
    assert "by Bob" in text
    assert "path=工程/部署.md" not in text


@pytest.mark.asyncio
async def test_list_repo_docs_cache_requires_team_for_duplicate_repo(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_repo_cache(docs_dir, data_dir)
    _write_repo_docs(docs_dir)

    tool = ListRepoDocsTool(plugin=_Plugin(docs_dir, data_dir))

    text = await tool.run(None, repo_name="工程")

    assert "找到多个知识库「工程」，请指定 team_id" in text
    assert "team_id=default" in text
    assert "team_id=other" in text


@pytest.mark.asyncio
async def test_list_repo_docs_filesystem_fallback_requires_team_for_duplicate_repo(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_repo_docs(docs_dir)

    tool = ListRepoDocsTool(plugin=_Plugin(docs_dir, data_dir))

    ambiguous = await tool.run(None, repo_name="工程")
    scoped = await tool.run(None, repo_name="工程", team_id="other")

    assert "找到多个知识库「工程」，请指定 team_id" in ambiguous
    assert "工程 (team_id=default, path_prefix=工程)" in ambiguous
    assert "工程 (team_id=other, path_prefix=other/工程)" in ambiguous
    assert "scope: team_id=other, repository=工程" in scoped
