"""Build NovaBot chunk indexes from synchronized Markdown files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import yaml

from astrbot.api import logger

from .chunk_store import ChunkStore
from .chunking import split_markdown
from .models import DEFAULT_TEAM_ID, DEFAULT_TEAM_NAME, scoped_document_id


def _read_frontmatter(markdown: str) -> tuple[dict, str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    parts = markdown.split("---\n", 2)
    if len(parts) != 3:
        return {}, markdown
    try:
        return yaml.safe_load(parts[1].strip()) or {}, parts[2]
    except yaml.YAMLError:
        return {}, parts[2]


def _source_url(base_url: str, namespace: str, slug: str) -> str:
    if not namespace or not slug:
        return ""
    site_url = base_url.rstrip("/")
    if site_url.endswith("/api/v2"):
        site_url = site_url[:-7]
    elif site_url.endswith("/api"):
        site_url = site_url[:-4]
    return f"{site_url}/{namespace}/{slug}"


def _load_repos(docs_dir: Path) -> dict[str, dict]:
    repos_file = docs_dir / ".repos.json"
    if not repos_file.exists():
        return {}
    try:
        repos = json.loads(repos_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    by_name = {}
    for repo in repos if isinstance(repos, list) else []:
        name = str(repo.get("name") or "")
        namespace = str(repo.get("namespace") or "")
        if name:
            by_name[name] = repo
        if namespace:
            by_name[namespace] = repo
    return by_name


def _repo_dir_from_rel_path(rel_path: str, repos: dict[str, dict]) -> str:
    parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
    if not parts:
        return ""
    repo_names = {
        key
        for key, repo in repos.items()
        if key and key in {str(repo.get("name") or ""), str(repo.get("namespace") or "")}
    }
    if parts[0] in repo_names or len(parts) == 1:
        return parts[0]
    if len(parts) >= 2:
        return parts[1]
    return parts[0]


def rebuild_chunk_index_from_sync(
    *,
    docs_dir: Path | str,
    chunk_store: ChunkStore,
    yuque_base_url: str = "https://www.yuque.com/api/v2",
    chunk_size: int = 1200,
    chunk_overlap: int = 180,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """Rebuild the chunk store from synchronized Markdown files."""

    docs_path = Path(docs_dir)
    chunk_store.clear()
    if not docs_path.exists():
        return {"documents": 0, "chunks": 0, "errors": 0}

    md_files = sorted(docs_path.rglob("*.md"))
    repos = _load_repos(docs_path)
    total_chunks = 0
    errors = 0

    for index, md_file in enumerate(md_files, 1):
        try:
            raw = md_file.read_text(encoding="utf-8")
            fm, _ = _read_frontmatter(raw)
            rel_path = str(md_file.relative_to(docs_path)).replace("\\", "/")
            repo_dir = _repo_dir_from_rel_path(rel_path, repos)
            repo_info = repos.get(str(fm.get("book_namespace") or "")) or repos.get(
                str(fm.get("book_name") or "")
            ) or repos.get(repo_dir) or {}

            team_id = str(fm.get("team_id") or repo_info.get("team_id") or DEFAULT_TEAM_ID)
            team_name = str(fm.get("team_name") or repo_info.get("team_name") or DEFAULT_TEAM_NAME)
            raw_document_id = str(fm.get("id") or fm.get("yuque_id") or rel_path)
            document_id = scoped_document_id(team_id, raw_document_id)
            namespace = str(
                fm.get("book_namespace")
                or repo_info.get("namespace")
                or ""
            )
            slug = str(fm.get("slug") or "")
            chunks = split_markdown(
                document_id,
                raw,
                title=str(fm.get("title") or md_file.stem),
                team_id=team_id,
                team_name=team_name,
                repository=str(fm.get("book_name") or repo_info.get("name") or repo_dir),
                namespace=namespace,
                slug=slug,
                file_path=rel_path,
                source_url=_source_url(yuque_base_url, namespace, slug),
                author=str(fm.get("author") or ""),
                updated_at=str(fm.get("updated_at") or ""),
                size=chunk_size,
                overlap=chunk_overlap,
            )
            chunk_store.save_document_chunks(document_id, chunks)
            total_chunks += len(chunks)
        except Exception as exc:
            errors += 1
            logger.warning(f"[ChunkIndex] 构建 chunk 失败: {md_file}: {exc}")
        finally:
            if progress_callback:
                progress_callback(index, len(md_files))

    return {"documents": len(md_files), "chunks": total_chunks, "errors": errors}


def upsert_chunk_from_markdown_file(
    *,
    docs_dir: Path | str,
    file_path: Path | str,
    chunk_store: ChunkStore,
    yuque_base_url: str = "https://www.yuque.com/api/v2",
    chunk_size: int = 1200,
    chunk_overlap: int = 180,
) -> dict:
    """Update chunk rows for a single synchronized Markdown file."""

    docs_path = Path(docs_dir)
    md_file = Path(file_path)
    raw = md_file.read_text(encoding="utf-8")
    fm, _ = _read_frontmatter(raw)
    rel_path = str(md_file.relative_to(docs_path)).replace("\\", "/")
    repos = _load_repos(docs_path)
    repo_dir = _repo_dir_from_rel_path(rel_path, repos)
    repo_info = repos.get(str(fm.get("book_namespace") or "")) or repos.get(
        str(fm.get("book_name") or "")
    ) or repos.get(repo_dir) or {}

    team_id = str(fm.get("team_id") or repo_info.get("team_id") or DEFAULT_TEAM_ID)
    team_name = str(fm.get("team_name") or repo_info.get("team_name") or DEFAULT_TEAM_NAME)
    raw_document_id = str(fm.get("id") or fm.get("yuque_id") or rel_path)
    document_id = scoped_document_id(team_id, raw_document_id)
    namespace = str(fm.get("book_namespace") or repo_info.get("namespace") or "")
    slug = str(fm.get("slug") or "")
    chunks = split_markdown(
        document_id,
        raw,
        title=str(fm.get("title") or md_file.stem),
        team_id=team_id,
        team_name=team_name,
        repository=str(fm.get("book_name") or repo_info.get("name") or repo_dir),
        namespace=namespace,
        slug=slug,
        file_path=rel_path,
        source_url=_source_url(yuque_base_url, namespace, slug),
        author=str(fm.get("author") or ""),
        updated_at=str(fm.get("updated_at") or ""),
        size=chunk_size,
        overlap=chunk_overlap,
    )
    chunk_store.save_document_chunks(document_id, chunks)
    return {"document_id": document_id, "chunks": len(chunks)}
