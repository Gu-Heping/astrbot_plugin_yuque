from novabot.chunk_indexer import rebuild_chunk_index_from_sync, upsert_chunk_from_markdown_file
from novabot.chunk_store import ChunkStore


def test_rebuild_chunk_index_from_synced_markdown(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    repo_dir = docs_dir / "工程"
    repo_dir.mkdir(parents=True)
    (docs_dir / ".repos.json").write_text(
        '[{"team_id":"nova","team_name":"NOVA","name":"工程","namespace":"nova/eng"}]',
        encoding="utf-8",
    )
    (repo_dir / "部署.md").write_text(
        """---
id: 42
title: 部署说明
slug: deploy
book_name: 工程
book_namespace: nova/eng
author: Alice
updated_at: '2026-01-01'
---

# 部署

NovaBot 多团队检索内核部署说明。
""",
        encoding="utf-8",
    )
    store = ChunkStore(tmp_path / "chunks.db")

    result = rebuild_chunk_index_from_sync(docs_dir=docs_dir, chunk_store=store)
    chunks = store.all_chunks()

    assert result["documents"] == 1
    assert result["chunks"] == 1
    assert chunks[0].document_id == "nova:42"
    assert chunks[0].team_id == "nova"
    assert chunks[0].repository == "工程"
    assert chunks[0].source_url.endswith("/nova/eng/deploy")


def test_upsert_chunk_from_markdown_file_replaces_existing_chunks(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    repo_dir = docs_dir / "工程"
    repo_dir.mkdir(parents=True)
    doc_file = repo_dir / "部署.md"
    store = ChunkStore(tmp_path / "chunks.db")

    doc_file.write_text(
        """---
id: 42
title: 部署说明
team_id: nova
team_name: NOVA
book_name: 工程
---

旧内容。
""",
        encoding="utf-8",
    )
    upsert_chunk_from_markdown_file(docs_dir=docs_dir, file_path=doc_file, chunk_store=store)
    doc_file.write_text(
        """---
id: 42
title: 部署说明
team_id: nova
team_name: NOVA
book_name: 工程
---

新内容，包含多团队检索。
""",
        encoding="utf-8",
    )
    upsert_chunk_from_markdown_file(docs_dir=docs_dir, file_path=doc_file, chunk_store=store)

    chunks = store.get_document_chunks("nova:42")
    assert len(chunks) == 1
    assert "新内容" in chunks[0].content
    assert "旧内容" not in chunks[0].content


def test_chunk_indexer_keeps_same_yuque_id_isolated_by_team(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    default_repo = docs_dir / "工程"
    other_repo = docs_dir / "other" / "工程"
    default_repo.mkdir(parents=True)
    other_repo.mkdir(parents=True)
    (default_repo / "部署.md").write_text(
        """---
id: 42
title: 默认部署
team_id: default
team_name: NOVA
book_name: 工程
---

默认团队内容。
""",
        encoding="utf-8",
    )
    (other_repo / "部署.md").write_text(
        """---
id: 42
title: 其他部署
team_id: other
team_name: Other
book_name: 工程
---

其他团队内容。
""",
        encoding="utf-8",
    )
    store = ChunkStore(tmp_path / "chunks.db")

    result = rebuild_chunk_index_from_sync(docs_dir=docs_dir, chunk_store=store)

    assert result["documents"] == 2
    assert store.get_document_chunks("42")[0].team_id == "default"
    assert store.get_document_chunks("other:42")[0].team_id == "other"
    contents = {chunk.document_id: chunk.content for chunk in store.all_chunks()}
    assert "默认团队内容" in contents["42"]
    assert "其他团队内容" in contents["other:42"]


def test_chunk_indexer_uses_repo_dir_after_team_prefix_when_frontmatter_missing_team(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    repo_dir = docs_dir / "other" / "工程"
    repo_dir.mkdir(parents=True)
    (docs_dir / ".repos.json").write_text(
        '[{"team_id":"other","team_name":"Other","name":"工程","namespace":"other/eng"}]',
        encoding="utf-8",
    )
    (repo_dir / "部署.md").write_text(
        """---
id: 42
title: 部署说明
---

其他团队路径推断内容。
""",
        encoding="utf-8",
    )
    store = ChunkStore(tmp_path / "chunks.db")

    rebuild_chunk_index_from_sync(docs_dir=docs_dir, chunk_store=store)
    chunks = store.get_document_chunks("other:42")

    assert chunks[0].team_id == "other"
    assert chunks[0].repository == "工程"


def test_chunk_indexer_keeps_first_segment_for_nested_doc_without_repo_cache(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    repo_dir = docs_dir / "工程" / "指南"
    repo_dir.mkdir(parents=True)
    (repo_dir / "部署.md").write_text(
        """---
id: 42
title: 部署说明
---

默认团队嵌套路径内容。
""",
        encoding="utf-8",
    )
    store = ChunkStore(tmp_path / "chunks.db")

    rebuild_chunk_index_from_sync(docs_dir=docs_dir, chunk_store=store)
    chunks = store.get_document_chunks("42")

    assert chunks[0].team_id == "default"
    assert chunks[0].repository == "工程"


def test_chunk_indexer_does_not_treat_default_repo_name_as_team_prefix(tmp_path):
    docs_dir = tmp_path / "yuque_docs"
    repo_dir = docs_dir / "other" / "指南"
    repo_dir.mkdir(parents=True)
    (docs_dir / ".repos.json").write_text(
        """
        [
          {"team_id":"default","team_name":"NOVA","name":"other","namespace":"nova/other"},
          {"team_id":"other","team_name":"Other","name":"工程","namespace":"other/eng"}
        ]
        """,
        encoding="utf-8",
    )
    (repo_dir / "部署.md").write_text(
        """---
id: 42
title: 部署说明
---

默认团队仓库名与非默认 team_id 冲突。
""",
        encoding="utf-8",
    )
    store = ChunkStore(tmp_path / "chunks.db")

    rebuild_chunk_index_from_sync(docs_dir=docs_dir, chunk_store=store)
    chunks = store.get_document_chunks("42")

    assert chunks[0].team_id == "default"
    assert chunks[0].repository == "other"
