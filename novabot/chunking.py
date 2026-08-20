"""Markdown-aware chunk construction for NovaBot knowledge documents."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .models import DEFAULT_TEAM_ID, DEFAULT_TEAM_NAME, Chunk, scoped_document_id


def _stable_chunk_id(document_id: str, chunk_index: int, content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"{document_id}:{chunk_index}:{digest}"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def strip_frontmatter_and_meta_table(body: str) -> str:
    if body.startswith("---\n"):
        parts = body.split("---\n", 2)
        if len(parts) == 3:
            body = parts[2]
    return re.sub(
        r"\A\s*\|[^\n]*\|\n\|(?:[-: ]+\|)+\n(?:\|[^\n]*\|\n?)*",
        "",
        body,
    ).lstrip()


def _clean_text(text: str) -> str:
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _split_by_markdown_boundaries(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            blocks.append("\n".join(current).strip())
            current.clear()

    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"#{1,6}\s+.*", stripped):
            flush()
            blocks.append(stripped)
        elif not stripped:
            flush()
        else:
            current.append(line)
    flush()

    merged: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if re.fullmatch(r"#{1,6}\s+.*", block) and i + 1 < len(blocks):
            merged.append(f"{block}\n\n{blocks[i + 1]}")
            i += 2
        else:
            merged.append(block)
            i += 1
    return [b for b in merged if b.strip()]


def _boundary_cut(text: str, size: int) -> int:
    if len(text) <= size:
        return len(text)
    for pattern in (r"[。！？\.\?\!]", r"[；;]", r"[\n]", r"[\s]"):
        for match in re.finditer(pattern, text[:size]):
            pos = match.end()
            if pos >= size * 0.3:
                return pos
    return size


def split_markdown(
    document_id: str,
    body: str,
    *,
    title: str = "",
    team_id: str = DEFAULT_TEAM_ID,
    team_name: str = DEFAULT_TEAM_NAME,
    repository: str = "",
    namespace: str = "",
    slug: str = "",
    file_path: str = "",
    source_url: str = "",
    author: str = "",
    updated_at: str = "",
    size: int = 1200,
    overlap: int = 180,
) -> list[Chunk]:
    """Split a Markdown document into stable metadata-rich chunks."""

    if size < 200 or not 0 <= overlap < size // 2:
        raise ValueError("invalid chunk settings")
    body = strip_frontmatter_and_meta_table(body)
    if not body.strip():
        return []

    parts: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current.strip():
            parts.append(current.strip())
            current = ""

    for block in _split_by_markdown_boundaries(body):
        if len(block) > size:
            flush_current()
            step = max(1, size - overlap)
            start = 0
            while start < len(block):
                end = _boundary_cut(block[start:], size) + start
                piece = block[start:end].strip()
                if piece:
                    parts.append(piece)
                next_start = min(len(block), start + step)
                start = next_start if next_start > start else start + step
            continue
        if current and len(current) + len(block) + 2 > size:
            flush_current()
        current = f"{current}\n\n{block}" if current else block
    flush_current()

    chunks: list[Chunk] = []
    for index, part in enumerate(parts):
        cleaned = _clean_text(part)
        if len(cleaned) < 3:
            continue
        chunks.append(
            Chunk(
                chunk_id=_stable_chunk_id(document_id, index, cleaned),
                document_id=document_id,
                chunk_index=index,
                content=cleaned,
                content_hash=_content_hash(cleaned),
                title=title,
                team_id=team_id or DEFAULT_TEAM_ID,
                team_name=team_name or DEFAULT_TEAM_NAME,
                repository=repository,
                namespace=namespace,
                slug=slug,
                file_path=file_path,
                source_url=source_url,
                author=author,
                updated_at=updated_at,
            )
        )
    return chunks


def split_doc_record(row: dict[str, Any], *, size: int = 1200, overlap: int = 180) -> list[Chunk]:
    team_id = str(row.get("team_id") or DEFAULT_TEAM_ID)
    return split_markdown(
        scoped_document_id(team_id, row.get("yuque_id") or row.get("id") or ""),
        str(row.get("body") or row.get("content") or ""),
        title=str(row.get("title") or ""),
        team_id=team_id,
        team_name=str(row.get("team_name") or DEFAULT_TEAM_NAME),
        repository=str(row.get("book_name") or row.get("repository") or ""),
        namespace=str(row.get("book_namespace") or row.get("namespace") or ""),
        slug=str(row.get("slug") or ""),
        file_path=str(row.get("file_path") or row.get("path") or ""),
        source_url=str(row.get("url") or row.get("source_url") or ""),
        author=str(row.get("author") or ""),
        updated_at=str(row.get("updated_at") or ""),
        size=size,
        overlap=overlap,
    )
