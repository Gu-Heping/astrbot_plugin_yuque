"""Persistent chunk metadata store for NovaBot retrieval."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .models import DEFAULT_TEAM_ID, DEFAULT_TEAM_NAME, Chunk


class ChunkStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        if self._conn is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                team_id TEXT NOT NULL DEFAULT 'default',
                team_name TEXT NOT NULL DEFAULT 'NOVA',
                repository TEXT NOT NULL DEFAULT '',
                namespace TEXT NOT NULL DEFAULT '',
                slug TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        for column, default in (
            ("team_id", DEFAULT_TEAM_ID),
            ("team_name", DEFAULT_TEAM_NAME),
            ("author", ""),
        ):
            self._ensure_column(column, f"TEXT NOT NULL DEFAULT '{default}'")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_team ON chunks(team_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repository)")
        self._conn.commit()

    def _ensure_column(self, name: str, definition: str) -> None:
        assert self._conn is not None
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if name not in columns:
            self._conn.execute(f"ALTER TABLE chunks ADD COLUMN {name} {definition}")

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _row_to_chunk(self, row: sqlite3.Row) -> Chunk:
        return Chunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            chunk_index=row["chunk_index"],
            content=row["content"],
            content_hash=row["content_hash"],
            title=row["title"],
            team_id=row["team_id"],
            team_name=row["team_name"],
            repository=row["repository"],
            namespace=row["namespace"],
            slug=row["slug"],
            file_path=row["file_path"],
            source_url=row["source_url"],
            author=row["author"],
            updated_at=row["updated_at"],
        )

    def save_document_chunks(self, document_id: str, chunks: list[Chunk]) -> None:
        conn = self._ensure_open()
        with conn:
            conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            conn.executemany(
                """
                INSERT INTO chunks
                (chunk_id, document_id, chunk_index, content, content_hash, title,
                 team_id, team_name, repository, namespace, slug, file_path,
                 source_url, author, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        c.chunk_id,
                        c.document_id,
                        c.chunk_index,
                        c.content,
                        c.content_hash,
                        c.title,
                        c.team_id,
                        c.team_name,
                        c.repository,
                        c.namespace,
                        c.slug,
                        c.file_path,
                        c.source_url,
                        c.author,
                        c.updated_at,
                    )
                    for c in chunks
                ],
            )

    def delete_document(self, document_id: str) -> int:
        conn = self._ensure_open()
        with conn:
            cur = conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            return cur.rowcount

    def all_chunks(self) -> list[Chunk]:
        conn = self._ensure_open()
        rows = conn.execute("SELECT * FROM chunks ORDER BY document_id, chunk_index").fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def get_document_chunks(self, document_id: str) -> list[Chunk]:
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT * FROM chunks WHERE document_id=? ORDER BY chunk_index",
            (document_id,),
        ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def clear(self) -> None:
        conn = self._ensure_open()
        with conn:
            conn.execute("DELETE FROM chunks")

    def chunk_count(self) -> int:
        conn = self._ensure_open()
        row = conn.execute("SELECT count(*) FROM chunks").fetchone()
        return int(row[0]) if row else 0

    def content_signature(self) -> str:
        conn = self._ensure_open()
        rows = conn.execute("SELECT content_hash FROM chunks ORDER BY chunk_id").fetchall()
        combined = "".join(row[0] for row in rows)
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def __enter__(self) -> "ChunkStore":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False
