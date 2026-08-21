import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from novabot.chunk_store import ChunkStore
from novabot.chunking import split_markdown
from novabot.knowledge_core import KnowledgeCore
from novabot.keyword_index import ChunkKeywordIndex
from novabot.models import Chunk, RetrievalResult


@pytest.mark.asyncio
async def test_knowledge_core_scopes_keyword_results_by_team(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    chunks = []
    chunks.extend(
        split_markdown(
            "1",
            "# 部署\n\nNovaBot 多团队检索内核部署说明",
            title="部署说明",
            team_id="nova",
            team_name="NOVA",
            repository="工程",
            namespace="nova/eng",
            file_path="工程/部署.md",
            size=220,
            overlap=40,
        )
    )
    chunks.extend(
        split_markdown(
            "2",
            "# 部署\n\n另一个团队的部署说明",
            title="部署说明",
            team_id="other",
            team_name="Other",
            repository="工程",
            namespace="other/eng",
            file_path="工程/部署.md",
            size=220,
            overlap=40,
        )
    )
    store.save_document_chunks("1", [c for c in chunks if c.document_id == "1"])
    store.save_document_chunks("2", [c for c in chunks if c.document_id == "2"])

    core = KnowledgeCore(store, keyword_index=ChunkKeywordIndex())
    results = await core.search("部署说明", scope={"team_id": "nova"}, top_k=5)

    assert results
    assert {result.chunk.team_id for result in results} == {"nova"}
    assert all("keyword" in result.methods for result in results)


@pytest.mark.asyncio
async def test_knowledge_core_scopes_by_namespace_and_requires_time_metadata(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    store.save_document_chunks(
        "1",
        split_markdown(
            "1",
            "# 部署\n\nNovaBot 命名空间范围检索说明",
            title="部署说明",
            team_id="nova",
            team_name="NOVA",
            repository="工程",
            namespace="nova/eng",
            file_path="工程/部署.md",
            updated_at="2026-02-01",
            size=220,
            overlap=40,
        ),
    )
    store.save_document_chunks(
        "2",
        split_markdown(
            "2",
            "# 部署\n\n缺少更新时间的范围检索说明",
            title="部署说明",
            team_id="nova",
            team_name="NOVA",
            repository="工程",
            namespace="nova/archive",
            file_path="工程/归档.md",
            updated_at="",
            size=220,
            overlap=40,
        ),
    )

    core = KnowledgeCore(store, keyword_index=ChunkKeywordIndex())
    results = await core.search(
        "范围检索说明",
        scope={"repository": "nova/eng", "updated_after": "2026-01-01"},
        top_k=5,
    )

    assert [result.chunk.namespace for result in results] == ["nova/eng"]


@pytest.mark.asyncio
async def test_knowledge_core_filters_scope_before_keyword_truncation(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    for i in range(8):
        store.save_document_chunks(
            f"other:{i}",
            split_markdown(
                f"other:{i}",
                "# 部署\n\n部署说明 通用内容",
                title="部署说明",
                team_id="other",
                team_name="Other",
                size=220,
                overlap=40,
            ),
        )
    store.save_document_chunks(
        "nova:1",
        split_markdown(
            "nova:1",
            "# 部署\n\n部署说明 Nova 专属内容",
            title="部署说明",
            team_id="nova",
            team_name="NOVA",
            size=220,
            overlap=40,
        ),
    )

    core = KnowledgeCore(store, keyword_index=ChunkKeywordIndex())
    results = await core.search("部署说明", scope={"team_id": "nova"}, top_k=1)

    assert [result.chunk.team_id for result in results] == ["nova"]


@pytest.mark.asyncio
async def test_knowledge_core_concurrent_searches_share_complete_keyword_index(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    store.save_document_chunks(
        "nova:1",
        split_markdown(
            "nova:1",
            "# 并发检索\n\nNova 并发检索内容",
            title="并发检索",
            team_id="nova",
            team_name="NOVA",
            size=220,
            overlap=40,
        ),
    )
    core = KnowledgeCore(store, keyword_index=ChunkKeywordIndex())

    results = await asyncio.gather(
        core.search("并发检索", scope={"team_id": "nova"}, top_k=1),
        core.search("并发检索", scope={"team_id": "nova"}, top_k=1),
    )

    assert all(batch and batch[0].chunk.document_id == "nova:1" for batch in results)


def test_chunk_store_can_be_read_from_worker_thread(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    store.save_document_chunks(
        "nova:1",
        split_markdown("nova:1", "# 标题\n\n线程安全内容", team_id="nova", size=220, overlap=40),
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        count = pool.submit(store.chunk_count).result()

    assert count == 1


def test_hybrid_merge_boosts_dual_method_hit_without_lowering_best_score(tmp_path):
    chunk = Chunk(
        chunk_id="c1",
        document_id="d1",
        chunk_index=0,
        content="混合检索",
        content_hash="h",
    )
    core = KnowledgeCore(ChunkStore(tmp_path / "chunks.db"))

    merged = core._merge(
        [RetrievalResult(chunk=chunk, score=0.9, keyword_score=0.9, methods=("keyword",))],
        [RetrievalResult(chunk=chunk, score=0.1, vector_score=0.1, methods=("semantic",))],
    )

    assert merged[0].score >= 0.9


def test_keyword_index_does_not_promote_low_coverage_match_to_high_score():
    chunk = Chunk(
        chunk_id="c1",
        document_id="d1",
        chunk_index=0,
        content="只有 部署 这个偶然词被命中",
        content_hash="h",
    )
    index = ChunkKeywordIndex()
    index.build([chunk])

    hits = index.search("部署 认证 缓存 权限", top_k=1)

    assert hits
    assert hits[0].score < 0.35
