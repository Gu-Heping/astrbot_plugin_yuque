from __future__ import annotations

from dataclasses import dataclass

import pytest

from novabot.doc_index import DocIndex
from novabot.tools.search import GrepLocalDocsTool


@dataclass
class _Storage:
    docs_dir: object
    data_dir: object


class _Plugin:
    def __init__(self, docs_dir, data_dir):
        self.storage = _Storage(docs_dir=docs_dir, data_dir=data_dir)


def _write_doc(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_index(data_dir):
    index = DocIndex(str(data_dir / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 1,
            "title": "默认部署",
            "author": "Alice",
            "team_id": "default",
            "team_name": "NOVA",
            "book_name": "工程",
            "updated_at": "2026-01-01",
            "file_path": "工程/部署.md",
        }
    )
    index.add_doc(
        {
            "yuque_id": 1,
            "title": "其他部署",
            "author": "Bob",
            "team_id": "other",
            "team_name": "Other",
            "book_name": "工程",
            "updated_at": "2026-02-01",
            "file_path": "other/工程/部署.md",
        }
    )


@pytest.mark.asyncio
async def test_grep_local_docs_filters_by_team_author_and_time(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_doc(docs_dir / "工程" / "部署.md", "# 默认部署\n\n范围检索 只属于默认团队。")
    _write_doc(docs_dir / "other" / "工程" / "部署.md", "# 其他部署\n\n范围检索 只属于其他团队。")
    _seed_index(data_dir)

    tool = GrepLocalDocsTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(
        None,
        keyword="范围检索",
        team_id="other",
        author="Bob",
        updated_after="2026-01-15",
    )

    assert "其他部署" in text
    assert "📁 other/工程/部署.md" in text
    assert "📁 工程/部署.md" not in text
    assert "默认部署" not in text


@pytest.mark.asyncio
async def test_grep_local_docs_repo_filter_uses_metadata_index_for_team_paths(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_doc(docs_dir / "工程" / "部署.md", "# 默认部署\n\n范围检索 只属于默认团队。")
    _write_doc(docs_dir / "other" / "工程" / "部署.md", "# 其他部署\n\n范围检索 只属于其他团队。")
    _seed_index(data_dir)

    tool = GrepLocalDocsTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(None, keyword="范围检索", repo_filter="工程")

    assert "📁 工程/部署.md" in text
    assert "📁 other/工程/部署.md" in text


@pytest.mark.asyncio
async def test_grep_local_docs_scope_requires_metadata_index(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _write_doc(docs_dir / "other" / "工程" / "部署.md", "# 其他部署\n\n范围检索")

    tool = GrepLocalDocsTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(None, keyword="范围检索", team_id="other")

    assert "指定范围内没有可搜索文档" in text
