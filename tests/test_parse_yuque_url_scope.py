from __future__ import annotations

from dataclasses import dataclass

import pytest

from novabot.doc_index import DocIndex
from novabot.tools.search import ParseYuqueUrlTool


@dataclass
class _Storage:
    docs_dir: object
    data_dir: object


class _Plugin:
    def __init__(self, docs_dir, data_dir):
        self.storage = _Storage(docs_dir=docs_dir, data_dir=data_dir)
        self.yuque_base_url = "https://www.yuque.com/api/v2"


@pytest.mark.asyncio
async def test_parse_yuque_url_selects_team_by_namespace_and_returns_evidence(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()

    default_doc = docs_dir / "工程" / "部署.md"
    other_doc = docs_dir / "other" / "工程" / "部署.md"
    default_doc.parent.mkdir(parents=True)
    other_doc.parent.mkdir(parents=True)
    default_doc.write_text("# 部署\n\n默认团队部署内容。", encoding="utf-8")
    other_doc.write_text("# 部署\n\nOther 团队部署内容。", encoding="utf-8")

    index = DocIndex(str(data_dir / "doc_index.db"))
    index.add_doc(
        {
            "yuque_id": 42,
            "title": "默认部署",
            "slug": "deploy",
            "team_id": "default",
            "team_name": "NOVA",
            "book_name": "工程",
            "book_namespace": "nova/eng",
            "file_path": "工程/部署.md",
            "url": "https://www.yuque.com/nova/eng/deploy",
        }
    )
    index.add_doc(
        {
            "yuque_id": 42,
            "title": "Other 部署",
            "slug": "deploy",
            "team_id": "other",
            "team_name": "Other",
            "book_name": "工程",
            "book_namespace": "other/eng",
            "file_path": "other/工程/部署.md",
            "url": "https://www.yuque.com/other/eng/deploy",
        }
    )

    tool = ParseYuqueUrlTool()
    tool.plugin = _Plugin(docs_dir, data_dir)

    text = await tool.run(None, "https://www.yuque.com/other/eng/deploy")

    assert "📄 《Other 部署》" in text
    assert "团队：Other (other)" in text
    assert "【Grounding Evidence】" in text
    assert "[E1] 《Other 部署》" in text
    assert "团队: Other (other)" in text
    assert "Other 团队部署内容。" in text
    assert "默认团队部署内容。" not in text
