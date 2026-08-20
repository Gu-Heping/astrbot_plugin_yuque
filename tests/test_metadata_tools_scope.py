from __future__ import annotations

from dataclasses import dataclass

import pytest

from novabot.doc_index import DocIndex
from novabot.tools.metadata import DocStatsTool, GetDocDetailsTool, ListAuthorsTool, SearchDocsTool


@dataclass
class _Storage:
    docs_dir: object
    data_dir: object


class _Plugin:
    def __init__(self, docs_dir, data_dir):
        self.storage = _Storage(docs_dir=docs_dir, data_dir=data_dir)
        self.yuque_base_url = "https://www.yuque.com/api/v2"


def _seed_index(data_dir):
    index = DocIndex(str(data_dir / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 42,
            "title": "默认团队部署",
            "author": "Alice",
            "team_id": "default",
            "team_name": "NOVA",
            "book_name": "工程",
            "book_namespace": "nova/eng",
            "updated_at": "2026-01-02",
            "word_count": 100,
            "file_path": "工程/部署.md",
        }
    )
    index.add_doc(
        {
            "yuque_id": 42,
            "title": "其他团队部署",
            "author": "Bob",
            "team_id": "other",
            "team_name": "Other",
            "book_name": "工程",
            "book_namespace": "other/eng",
            "updated_at": "2026-02-03",
            "word_count": 200,
            "file_path": "other/工程/部署.md",
        }
    )


@pytest.mark.asyncio
async def test_search_docs_filters_by_team_path_and_time(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _seed_index(data_dir)

    tool = SearchDocsTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(
        None,
        title="部署",
        team_id="other",
        path_prefix="other/工程",
        updated_after="2026-02-01",
    )

    assert "其他团队部署" in text
    assert "团队: Other (other)" in text
    assert "默认团队部署" not in text


@pytest.mark.asyncio
async def test_stats_and_authors_accept_team_scope(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _seed_index(data_dir)
    plugin = _Plugin(docs_dir, data_dir)

    stats_tool = DocStatsTool()
    stats_tool.plugin = plugin
    authors_tool = ListAuthorsTool()
    authors_tool.plugin = plugin

    stats = await stats_tool.run(None, team_id="other")
    authors = await authors_tool.run(None, team_id="other")

    assert "文档数: 1" in stats
    assert "总字数: 200" in stats
    assert "Bob" in authors
    assert "Alice" not in authors


@pytest.mark.asyncio
async def test_get_doc_details_disambiguates_yuque_id_by_team(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _seed_index(data_dir)

    tool = GetDocDetailsTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(None, yuque_id=42, team_id="other")

    assert '"title": "其他团队部署"' in text
    assert '"team_id": "other"' in text


@pytest.mark.asyncio
async def test_get_doc_details_reports_ambiguous_yuque_id_without_team(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _seed_index(data_dir)

    tool = GetDocDetailsTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(None, yuque_id=42)

    assert '"error": "multiple_matches"' in text
    assert "请用 team_id、path 或 url 精确指定" in text
    assert '"team_id": "default"' in text
    assert '"team_id": "other"' in text


@pytest.mark.asyncio
async def test_get_doc_details_include_content_returns_grounding_evidence(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _seed_index(data_dir)

    doc_path = docs_dir / "other" / "工程" / "部署.md"
    doc_path.parent.mkdir(parents=True)
    doc_path.write_text("# 部署\n\nOther 团队事实内容。", encoding="utf-8")

    tool = GetDocDetailsTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(None, yuque_id=42, team_id="other", include_content=True)

    assert "【Grounding Evidence】" in text
    assert "[E1] 《其他团队部署》" in text
    assert "团队: Other (other)" in text
    assert "Other 团队事实内容。" in text
    assert '"title": "其他团队部署"' in text
    assert '"content": "# 部署\\n\\nOther 团队事实内容。"' in text
