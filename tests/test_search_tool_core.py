import pytest

from novabot.chunk_store import ChunkStore
from novabot.chunking import split_markdown
from novabot.knowledge_core import KnowledgeCore
from novabot.tools.search import SearchKnowledgeBaseTool


class _Plugin:
    def __init__(self, store):
        self.chunk_store = store
        self.knowledge_core = KnowledgeCore(store)
        self.rag = None


class _LegacyRag:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, k=5, book_filter=None):
        self.calls.append({"query": query, "k": k, "book_filter": book_filter})
        return self.results[:k]


class _LegacyPlugin:
    def __init__(self, results):
        self.chunk_store = None
        self.knowledge_core = None
        self.rag = _LegacyRag(results)


@pytest.mark.asyncio
async def test_search_tool_uses_knowledge_core_scope(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    store.save_document_chunks(
        "1",
        split_markdown(
            "1",
            "# 检索\n\nNovaBot 支持 team + repository 范围检索。",
            title="检索指南",
            team_id="nova",
            team_name="NOVA",
            repository="工程",
            file_path="工程/检索.md",
            size=220,
            overlap=40,
        ),
    )
    store.save_document_chunks(
        "2",
        split_markdown(
            "2",
            "# 检索\n\n其他团队也有范围检索说明。",
            title="检索指南",
            team_id="other",
            team_name="Other",
            repository="工程",
            file_path="工程/检索.md",
            size=220,
            overlap=40,
        ),
    )

    tool = SearchKnowledgeBaseTool()
    tool.plugin = _Plugin(store)
    text = await tool.run(None, "范围检索", team_id="nova")

    assert "【Grounding Evidence】" in text
    assert "[E1]" in text
    assert "NOVA (nova)" in text
    assert "Other (other)" not in text
    assert "reliable=" in text


@pytest.mark.asyncio
async def test_search_tool_does_not_promote_unreliable_candidates(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    store.save_document_chunks(
        "1",
        split_markdown(
            "1",
            "# 模糊\n\n只有一个很弱的候选片段。",
            title="模糊文档",
            team_id="nova",
            team_name="NOVA",
            repository="工程",
            file_path="工程/模糊.md",
            size=220,
            overlap=40,
        ),
    )
    plugin = _Plugin(store)
    plugin.knowledge_core.reliable_threshold = 2.0
    tool = SearchKnowledgeBaseTool()
    tool.plugin = plugin

    text = await tool.run(None, "候选片段", team_id="nova")

    assert "未找到 reliable=true" in text
    assert "[E1]" not in text
    assert "候选检索结果" in text


@pytest.mark.asyncio
async def test_search_tool_filters_legacy_rag_fallback_by_scope():
    tool = SearchKnowledgeBaseTool()
    tool.plugin = _LegacyPlugin(
        [
            {
                "title": "Other 文档",
                "content": "其他团队内容",
                "team_id": "other",
                "team_name": "Other",
                "book_name": "工程",
                "author": "Bob",
                "source": "yuque:other/eng/deploy",
            },
            {
                "title": "NOVA 文档",
                "content": "Nova 团队内容",
                "team_id": "nova",
                "team_name": "NOVA",
                "book_name": "工程",
                "author": "Alice",
                "source": "yuque:nova/eng/deploy",
            },
        ]
    )

    text = await tool.run(None, "部署", team_id="nova", repository="工程", author="Alice")

    assert "NOVA 文档" in text
    assert "Nova 团队内容" in text
    assert "Other 文档" not in text
    assert tool.plugin.rag.calls[0]["k"] == 25
    assert tool.plugin.rag.calls[0]["book_filter"] == "工程"


@pytest.mark.asyncio
async def test_search_tool_legacy_rag_time_scope_requires_metadata():
    tool = SearchKnowledgeBaseTool()
    tool.plugin = _LegacyPlugin(
        [
            {
                "title": "无时间文档",
                "content": "旧 RAG 未携带更新时间",
                "team_id": "nova",
                "team_name": "NOVA",
                "book_name": "工程",
                "author": "Alice",
                "source": "yuque:nova/eng/deploy",
            }
        ]
    )

    text = await tool.run(None, "部署", team_id="nova", updated_after="2026-01-01")

    assert "指定范围内" in text
    assert "未找到" in text
    assert "无时间文档" not in text


@pytest.mark.asyncio
async def test_search_tool_legacy_rag_path_and_time_scope_can_match_metadata():
    tool = SearchKnowledgeBaseTool()
    tool.plugin = _LegacyPlugin(
        [
            {
                "title": "部署指南",
                "content": "带有更新时间与路径的旧 RAG 结果",
                "team_id": "nova",
                "team_name": "NOVA",
                "book_name": "工程",
                "author": "Alice",
                "file_path": "工程/指南/部署.md",
                "updated_at": "2026-02-01",
                "source": "yuque:nova/eng/deploy",
            },
            {
                "title": "旧部署指南",
                "content": "时间不在范围内",
                "team_id": "nova",
                "team_name": "NOVA",
                "book_name": "工程",
                "author": "Alice",
                "file_path": "工程/指南/旧部署.md",
                "updated_at": "2025-12-01",
                "source": "yuque:nova/eng/old",
            },
        ]
    )

    text = await tool.run(
        None,
        "部署",
        team_id="nova",
        path_prefix="工程/指南",
        updated_after="2026-01-01",
    )

    assert "部署指南" in text
    assert "带有更新时间与路径" in text
    assert "旧部署指南" not in text
