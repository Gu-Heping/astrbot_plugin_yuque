"""Adapters that let legacy RAG participate in KnowledgeCore hybrid search."""

from __future__ import annotations

import asyncio

from .chunk_store import ChunkStore
from .models import DEFAULT_TEAM_ID, DEFAULT_TEAM_NAME, Chunk, RetrievalResult, RetrievalScope


class RagVectorSearchAdapter:
    """Expose ``RAGEngine.search`` as a scoped KnowledgeCore vector search."""

    def __init__(self, rag, chunk_store: ChunkStore, *, reliable_threshold: float = 0.45):
        self.rag = rag
        self.chunk_store = chunk_store
        self.reliable_threshold = reliable_threshold

    async def __call__(
        self,
        query: str,
        top_k: int,
        scope: RetrievalScope,
    ) -> list[RetrievalResult]:
        book_filter = _legacy_book_filter(scope.repositories)
        raw_results = await asyncio.to_thread(
            self.rag.search,
            query,
            max(top_k * 3, 10),
            book_filter,
            False,
        )
        results: list[RetrievalResult] = []
        for rank, item in enumerate(raw_results, 1):
            chunk = self._chunk_from_result(item, scope)
            if chunk is None:
                continue
            vector_score = max(0.05, 1.0 - (rank - 1) * 0.08)
            results.append(
                RetrievalResult(
                    chunk=chunk,
                    score=vector_score,
                    vector_score=vector_score,
                    methods=("semantic",),
                    reliable=vector_score >= self.reliable_threshold and bool(chunk.content.strip()),
                )
            )
            if len(results) >= top_k:
                break
        return results

    def _chunk_from_result(self, item: dict, scope: RetrievalScope) -> Chunk | None:
        document_id = str(item.get("id") or item.get("yuque_id") or "").strip()
        chunks = self.chunk_store.get_document_chunks(document_id) if document_id else []
        for chunk in _rank_chunks_for_result(chunks, str(item.get("content") or "")):
            if scope.matches_doc(chunk.as_document()):
                return chunk

        fallback = _synthetic_chunk(item)
        if fallback and scope.matches_doc(fallback.as_document()):
            return fallback
        return None


def _rank_chunks_for_result(chunks: list[Chunk], content_hint: str) -> list[Chunk]:
    if not content_hint:
        return chunks
    hint = " ".join(content_hint.split()).casefold()
    return sorted(
        chunks,
        key=lambda chunk: 0 if hint and hint[:80] in chunk.content.casefold() else 1,
    )


def _synthetic_chunk(item: dict) -> Chunk | None:
    content = str(item.get("content") or "").strip()
    document_id = str(item.get("id") or item.get("yuque_id") or "").strip()
    if not content or not document_id:
        return None
    team_id = str(item.get("team_id") or DEFAULT_TEAM_ID)
    team_name = str(item.get("team_name") or DEFAULT_TEAM_NAME)
    source = str(item.get("source") or "")
    namespace, slug = _parse_source(source)
    return Chunk(
        chunk_id=f"rag:{document_id}",
        document_id=document_id,
        chunk_index=0,
        content=content,
        content_hash="",
        title=str(item.get("title") or ""),
        team_id=team_id,
        team_name=team_name,
        repository=str(item.get("book_name") or ""),
        namespace=namespace,
        slug=slug,
        file_path=str(item.get("file_path") or ""),
        source_url=source if source.startswith(("http://", "https://")) else "",
        author=str(item.get("author") or ""),
        updated_at=str(item.get("updated_at") or ""),
    )


def _parse_source(source: str) -> tuple[str, str]:
    if not source.startswith("yuque:"):
        return "", ""
    value = source.removeprefix("yuque:")
    if "/" not in value:
        return value, ""
    namespace, slug = value.rsplit("/", 1)
    return namespace, slug


def _legacy_book_filter(repositories: tuple[str, ...]) -> str | None:
    if len(repositories) != 1:
        return None
    repository = repositories[0]
    # A namespace such as nova/eng must be filtered after retrieval because the
    # legacy RAG backend only knows book_name in its native filter.
    if "/" in repository or "\\" in repository:
        return None
    return repository
