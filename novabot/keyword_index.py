"""Chunk-level keyword search with BM25-style scoring."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .models import Chunk


_STOP_WORDS = frozenset(
    "的 是 了 在 和 有 我 他 她 它 你 我们 你们 他们 这 那 这些 那些 "
    "什么 怎么 吗 呢 吧 啊 哦 嗯 对 不 没有 就是 可以 这个 那个 "
    "与 及 或 但 而 因为 所以 如果 虽然 然而 因此 之 其 所 被 把 让 "
    "to be a an the and or but in on at for with of is are was were".split()
)


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens: list[str] = []
    urls = re.findall(r"https?://[^\s<>()，。；：\"'（）]+", text)
    placeholders: dict[str, str] = {}
    for i, url in enumerate(urls):
        key = f"\x00URL{i}\x00"
        placeholders[key] = url
        text = text.replace(url, key, 1)
    for piece in re.findall(r"[a-z]+|[0-9]+|\x00URL\d+\x00|[一-鿿]", text):
        tokens.append(placeholders.get(piece, piece))
    chars = re.findall(r"[一-鿿]", text)
    tokens.extend("".join(chars[i : i + 2]) for i in range(len(chars) - 1))
    return tokens


def extract_query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for token in tokenize(query):
        if token in _STOP_WORDS or len(token) == 1:
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
    return terms


@dataclass(frozen=True)
class KeywordHit:
    chunk: Chunk
    score: float
    matched_terms: tuple[str, ...]
    title_match: bool = False
    phrase_match: bool = False


class ChunkKeywordIndex:
    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._chunks: dict[str, Chunk] = {}
        self._index: dict[str, list[tuple[str, int, int, int]]] = {}
        self._avg_len = 0.0

    def build(self, chunks: list[Chunk]) -> None:
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self._index = {}
        total_len = 0
        for chunk in chunks:
            title_tokens = tokenize(chunk.title)
            body_tokens = tokenize(chunk.content)
            path_tokens = tokenize(chunk.file_path)
            total_len += max(len(body_tokens), 1)
            counts = Counter(body_tokens + title_tokens + path_tokens)
            title_counts = Counter(title_tokens)
            path_counts = Counter(path_tokens)
            for term, freq in counts.items():
                self._index.setdefault(term, []).append(
                    (chunk.chunk_id, freq, title_counts.get(term, 0), path_counts.get(term, 0))
                )
        self._avg_len = total_len / max(len(chunks), 1)

    def search(self, query: str, top_k: int = 20) -> list[KeywordHit]:
        terms = extract_query_terms(query)
        if not terms or not self._chunks:
            return []
        total = max(len(self._chunks), 1)
        scores: dict[str, float] = {}
        matches: dict[str, set[str]] = {}
        title_hits: dict[str, int] = {}
        phrase_hits: dict[str, int] = {}

        for term in terms:
            postings = self._index.get(term, [])
            idf = math.log((total - len(postings) + 0.5) / (len(postings) + 0.5) + 1.0)
            for chunk_id, freq, title_count, path_count in postings:
                chunk = self._chunks[chunk_id]
                body_len = max(len(tokenize(chunk.content)), 1)
                denom = freq + self.k1 * (1 - self.b + self.b * body_len / max(self._avg_len, 1))
                bm25 = idf * (freq * (self.k1 + 1)) / max(denom, 0.001)
                boost = idf * (title_count * 2.0 + path_count)
                scores[chunk_id] = scores.get(chunk_id, 0.0) + bm25 + boost
                matches.setdefault(chunk_id, set()).add(term)
                title_hits[chunk_id] = title_hits.get(chunk_id, 0) + title_count

        if len(terms) > 1:
            phrase = "".join(terms[:2])
            for chunk_id, chunk in self._chunks.items():
                if phrase and phrase in (chunk.title + chunk.content + chunk.file_path).casefold():
                    phrase_hits[chunk_id] = phrase_hits.get(chunk_id, 0) + 1
                    scores[chunk_id] = scores.get(chunk_id, 0.0) * 1.15

        max_score = max(scores.values(), default=0.0)
        scale = max(max_score, 1.0)
        hits: list[KeywordHit] = []
        for chunk_id, raw_score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
            coverage = len(matches.get(chunk_id, set())) / len(terms)
            if coverage < 0.25:
                continue
            score = min(1.0, (raw_score / scale) * (1.0 + coverage * 0.2))
            hits.append(
                KeywordHit(
                    chunk=self._chunks[chunk_id],
                    score=score,
                    matched_terms=tuple(sorted(matches.get(chunk_id, set()))),
                    title_match=title_hits.get(chunk_id, 0) > 0,
                    phrase_match=phrase_hits.get(chunk_id, 0) > 0,
                )
            )
            if len(hits) >= top_k:
                break
        return hits
