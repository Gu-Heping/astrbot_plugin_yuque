from __future__ import annotations

from pathlib import Path

from novabot.rag import RAGEngine


class _Embeddings:
    def embed_query(self, text):
        return [0.1, 0.2, 0.3]


class _Collection:
    def __init__(self):
        self.deleted = []

    def delete(self, where):
        self.deleted.append(where)


class _Vectorstore:
    def __init__(self):
        self._collection = _Collection()
        self.added = []

    def add_documents(self, docs):
        self.added.extend(docs)


def _rag(tmp_path):
    rag = object.__new__(RAGEngine)
    rag.persist_directory = Path(tmp_path)
    rag.embeddings = _Embeddings()
    rag._vectorstore = _Vectorstore()
    rag._client = None
    rag._query_cache = {}
    return rag


def test_rag_delete_doc_uses_team_scoped_identity(tmp_path):
    rag = _rag(tmp_path)

    assert rag.delete_doc(42, team_id="other") is True

    assert rag.vectorstore._collection.deleted == [{"id": "other:42"}]


def test_rag_index_docs_keeps_scoped_and_raw_ids(tmp_path):
    rag = _rag(tmp_path)

    indexed = rag.index_docs(
        [
            {
                "id": 42,
                "team_id": "other",
                "team_name": "Other",
                "content": "多团队 RAG 身份测试",
                "title": "部署",
                "book_name": "工程",
                "file_path": "other/工程/部署.md",
                "created_at": "2026-01-01",
                "updated_at": "2026-02-01",
            }
        ]
    )

    assert indexed == 1
    metadata = rag.vectorstore.added[0].metadata
    assert metadata["id"] == "other:42"
    assert metadata["yuque_id"] == "42"
    assert metadata["team_id"] == "other"
    assert metadata["team_name"] == "Other"
    assert metadata["file_path"] == "other/工程/部署.md"
    assert metadata["created_at"] == "2026-01-01"
    assert metadata["updated_at"] == "2026-02-01"
