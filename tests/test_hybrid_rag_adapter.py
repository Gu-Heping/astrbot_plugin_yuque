from __future__ import annotations

import pytest

from novabot.chunk_store import ChunkStore
from novabot.chunking import split_markdown
from novabot.knowledge_core import KnowledgeCore
from novabot.models import RetrievalScope
from novabot.rag_adapter import RagVectorSearchAdapter


class _FakeRag:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, k=5, book_filter=None, use_cache=True):
        self.calls.append((query, k, book_filter, use_cache))
        return self.results


def _save_chunk(store, document_id, body, *, team_id, team_name):
    chunks = split_markdown(
        document_id,
        body,
        title="混合检索",
        team_id=team_id,
        team_name=team_name,
        repository="工程",
        file_path=f"{team_id}/工程/混合.md" if team_id != "default" else "工程/混合.md",
        size=220,
        overlap=40,
    )
    store.save_document_chunks(document_id, chunks)
    return chunks[0]


@pytest.mark.asyncio
async def test_rag_adapter_filters_semantic_results_by_scope(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    _save_chunk(store, "nova:42", "Nova 团队向量内容。", team_id="nova", team_name="NOVA")
    _save_chunk(store, "other:42", "Other 团队向量内容。", team_id="other", team_name="Other")
    rag = _FakeRag(
        [
            {"id": "other:42", "team_id": "other", "team_name": "Other", "content": "Other 团队向量内容。"},
            {"id": "nova:42", "team_id": "nova", "team_name": "NOVA", "content": "Nova 团队向量内容。"},
        ]
    )
    core = KnowledgeCore(
        store,
        vector_search=RagVectorSearchAdapter(rag, store),
        reliable_threshold=0.35,
    )

    results = await core.search("vector-only", top_k=5, scope={"team_id": "nova"})

    assert [result.chunk.document_id for result in results] == ["nova:42"]
    assert results[0].methods == ("semantic",)
    assert results[0].reliable is True


@pytest.mark.asyncio
async def test_hybrid_search_merges_keyword_and_semantic_methods(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    chunk = _save_chunk(
        store,
        "nova:42",
        "NovaBot 混合检索 会融合 keyword 和 semantic。",
        team_id="nova",
        team_name="NOVA",
    )
    rag = _FakeRag(
        [
            {
                "id": "nova:42",
                "team_id": "nova",
                "team_name": "NOVA",
                "content": chunk.content,
            }
        ]
    )
    core = KnowledgeCore(
        store,
        vector_search=RagVectorSearchAdapter(rag, store),
        reliable_threshold=0.35,
    )

    results = await core.search("混合检索", top_k=5, scope={"team_id": "nova"})

    assert len(results) == 1
    assert set(results[0].methods) == {"keyword", "semantic"}
    assert results[0].keyword_score > 0
    assert results[0].vector_score > 0
    assert results[0].reliable is True
    assert rag.calls[0][3] is False


@pytest.mark.asyncio
async def test_rag_adapter_does_not_push_namespace_as_legacy_book_filter(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    chunks = split_markdown(
        "nova:42",
        "# 命名空间\n\nNova 命名空间向量范围检索。",
        title="命名空间",
        team_id="nova",
        team_name="NOVA",
        repository="工程",
        namespace="nova/eng",
        file_path="工程/命名空间.md",
        updated_at="2026-02-01",
        size=220,
        overlap=40,
    )
    store.save_document_chunks("nova:42", chunks)
    rag = _FakeRag(
        [
            {
                "id": "nova:42",
                "team_id": "nova",
                "team_name": "NOVA",
                "book_name": "工程",
                "content": chunks[0].content,
            }
        ]
    )
    adapter = RagVectorSearchAdapter(rag, store)

    results = await adapter(
        "命名空间",
        top_k=5,
        scope=RetrievalScope.from_dict({"repository": "nova/eng"}),
    )

    assert [result.chunk.namespace for result in results] == ["nova/eng"]
    assert rag.calls[0][2] is None


@pytest.mark.asyncio
async def test_rag_adapter_synthetic_chunk_carries_path_and_time_scope(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    rag = _FakeRag(
        [
            {
                "id": "nova:42",
                "team_id": "nova",
                "team_name": "NOVA",
                "book_name": "工程",
                "content": "旧 RAG synthetic 结果可被 path/time scope 过滤。",
                "file_path": "工程/指南/部署.md",
                "updated_at": "2026-02-01",
                "source": "yuque:nova/eng/deploy",
            }
        ]
    )
    adapter = RagVectorSearchAdapter(rag, store)
    scope = RetrievalScope.from_dict(
        {
            "team_id": "nova",
            "path_prefix": "工程/指南",
            "updated_after": "2026-01-01",
        }
    )

    results = await adapter("synthetic", top_k=5, scope=scope)

    assert len(results) == 1
    assert results[0].chunk.file_path == "工程/指南/部署.md"
    assert results[0].chunk.updated_at == "2026-02-01"
