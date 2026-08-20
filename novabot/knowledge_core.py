"""Composable retrieval core for NovaBot knowledge facts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Awaitable, Callable

from .chunk_store import ChunkStore
from .keyword_index import ChunkKeywordIndex
from .models import Chunk, RetrievalResult, RetrievalScope

VectorSearch = Callable[[str, int, RetrievalScope], Awaitable[list[RetrievalResult]]]


class KnowledgeCore:
    """Hybrid-ready retrieval over chunks.

    The first implementation is deliberately keyword-first so it can run in
    tests and in installs without embeddings. A vector adapter can be injected
    later without changing the scope contract.
    """

    def __init__(
        self,
        chunk_store: ChunkStore,
        *,
        keyword_index: ChunkKeywordIndex | None = None,
        vector_search: VectorSearch | None = None,
        reliable_threshold: float = 0.35,
    ):
        self.chunk_store = chunk_store
        self.keyword_index = keyword_index or ChunkKeywordIndex()
        self.vector_search = vector_search
        self.reliable_threshold = reliable_threshold
        self._keyword_version: int | None = None

    async def _ensure_keyword_index(self) -> None:
        version = await asyncio.to_thread(self.chunk_store.version)
        if version == self._keyword_version:
            return
        chunks = await asyncio.to_thread(self.chunk_store.all_chunks)
        await asyncio.to_thread(self.keyword_index.build, chunks)
        self._keyword_version = version

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        scope: RetrievalScope | dict | None = None,
    ) -> list[RetrievalResult]:
        resolved_scope = (
            scope if isinstance(scope, RetrievalScope) else RetrievalScope.from_dict(scope)
        )
        await self._ensure_keyword_index()
        keyword_results = self._keyword_results(query, top_k * 4, resolved_scope)
        vector_results: list[RetrievalResult] = []
        if self.vector_search is not None:
            vector_results = await self.vector_search(query, top_k * 4, resolved_scope)
        merged = self._merge(keyword_results, vector_results)
        return sorted(merged, key=lambda item: item.score, reverse=True)[:top_k]

    def _keyword_results(
        self,
        query: str,
        top_k: int,
        scope: RetrievalScope,
    ) -> list[RetrievalResult]:
        results: list[RetrievalResult] = []
        for hit in self.keyword_index.search(query, top_k=top_k, scope=scope):
            score = hit.score
            reliable = score >= self.reliable_threshold and bool(hit.matched_terms)
            if hit.title_match or hit.phrase_match:
                reliable = reliable or score >= self.reliable_threshold * 0.8
            results.append(
                RetrievalResult(
                    chunk=hit.chunk,
                    score=score,
                    keyword_score=hit.score,
                    methods=("keyword",),
                    reliable=reliable,
                )
            )
        return results

    def _merge(
        self,
        keyword_results: list[RetrievalResult],
        vector_results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        merged: dict[str, RetrievalResult] = {}
        for result in [*keyword_results, *vector_results]:
            existing = merged.get(result.chunk.chunk_id)
            if existing is None:
                merged[result.chunk.chunk_id] = result
                continue
            keyword_score = max(existing.keyword_score, result.keyword_score)
            vector_score = max(existing.vector_score, result.vector_score)
            base_score = max(existing.score, result.score, keyword_score, vector_score)
            score = min(1.0, base_score + 0.12)
            merged[result.chunk.chunk_id] = RetrievalResult(
                chunk=_prefer_richer_chunk(existing.chunk, result.chunk),
                score=score,
                keyword_score=keyword_score,
                vector_score=vector_score,
                methods=tuple(dict.fromkeys([*existing.methods, *result.methods])),
                reliable=existing.reliable or result.reliable or score >= self.reliable_threshold,
            )
        return list(merged.values())


def _prefer_richer_chunk(a: Chunk, b: Chunk) -> Chunk:
    if len(b.content) > len(a.content):
        return replace(b)
    return replace(a)
