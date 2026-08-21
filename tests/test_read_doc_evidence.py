from __future__ import annotations

from dataclasses import dataclass

import pytest

from novabot.doc_index import DocIndex
from novabot.tools.search import ReadDocTool


@dataclass
class _Storage:
    docs_dir: object
    data_dir: object


class _Plugin:
    def __init__(self, docs_dir, data_dir):
        self.storage = _Storage(docs_dir=docs_dir, data_dir=data_dir)
        self.yuque_base_url = "https://www.yuque.com/api/v2"


@pytest.mark.asyncio
async def test_read_doc_outputs_grounding_evidence_with_metadata(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    doc_path = docs_dir / "工程" / "部署.md"
    doc_path.parent.mkdir()
    doc_path.write_text(
        """---
title: 部署指南
author: Alice
---

| 元信息 | 值 |
| --- | --- |
| 作者 | Alice |

# 部署指南

NovaBot 多团队同步生命周期需要按 team 隔离。
""",
        encoding="utf-8",
    )

    index = DocIndex(str(data_dir / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 42,
            "title": "部署指南",
            "author": "Alice",
            "team_id": "nova",
            "team_name": "NOVA",
            "book_name": "工程",
            "book_namespace": "nova/eng",
            "file_path": "工程/部署.md",
            "url": "https://www.yuque.com/nova/eng/deploy",
        }
    )

    tool = ReadDocTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(None, path="工程/部署.md")

    assert "【Grounding Evidence】" in text
    assert "[E1] 《部署指南》" in text
    assert "团队: NOVA (nova)" in text
    assert "知识库: 工程" in text
    assert "作者: Alice" in text
    assert "路径: 工程/部署.md" in text
    assert "NovaBot 多团队同步生命周期需要按 team 隔离。" in text
    assert "元信息" not in text
    assert "frontmatter" not in text


@pytest.mark.asyncio
async def test_read_doc_raw_mode_keeps_original_file_without_evidence(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    doc_path = docs_dir / "工程" / "原始.md"
    doc_path.parent.mkdir()
    doc_path.write_text("---\ntitle: 原始\n---\n\n# 原始\n", encoding="utf-8")

    tool = ReadDocTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(None, path="工程/原始.md", strip_metadata=False)

    assert "【Grounding Evidence】" not in text
    assert "---\ntitle: 原始\n---" in text
