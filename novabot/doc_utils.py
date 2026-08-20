"""
文档工具共享逻辑：语雀 URL 构建、链接解析、Markdown 读取
"""

import json
import re
from pathlib import Path
from typing import Any, Optional

import yaml


def yuque_web_base(api_base_url: str) -> str:
    """从 API base URL 推导语雀 Web 根地址"""
    base = (api_base_url or "https://www.yuque.com/api/v2").rstrip("/")
    if base.endswith("/api/v2"):
        return base[:-7]
    if base.endswith("/api"):
        return base[:-4]
    return base


def build_doc_url(namespace: str, slug: str, api_base_url: str) -> str:
    """拼接语雀文档 Web URL"""
    if not namespace or not slug:
        return ""
    web_base = yuque_web_base(api_base_url)
    return f"{web_base}/{namespace.strip('/')}/{slug}"


def parse_yuque_doc_url(url: str) -> Optional[tuple[str, str]]:
    """解析语雀文档链接，返回 (book_namespace, doc_slug)"""
    if not url:
        return None
    clean = url.strip().split("#")[0].split("?")[0].rstrip("/")
    pattern = r"https?://[\w-]+\.yuque\.com/([\w-]+/[\w-]+)/([\w-]+)$"
    match = re.match(pattern, clean)
    if match:
        return match.group(1), match.group(2)
    return None


def doc_record_to_public_dict(row: dict, api_base_url: str) -> dict:
    """将 DocIndex 行转为工具输出的标准字典"""
    namespace = row.get("book_namespace") or ""
    slug = row.get("slug") or ""
    return {
        "yuque_id": row.get("yuque_id"),
        "title": row.get("title") or "",
        "slug": slug,
        "author": row.get("author") or "",
        "team_id": row.get("team_id") or "",
        "team_name": row.get("team_name") or "",
        "book_name": row.get("book_name") or "",
        "book_namespace": namespace,
        "creator_id": row.get("creator_id"),
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "word_count": row.get("word_count") or 0,
        "file_path": row.get("file_path") or "",
        "url": build_doc_url(namespace, slug, api_base_url),
    }


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (metadata, body_with_table)"""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        metadata = yaml.safe_load(parts[1].strip()) or {}
    except yaml.YAMLError:
        metadata = {}
    return metadata if isinstance(metadata, dict) else {}, parts[2].strip()


def strip_meta_table(body: str) -> str:
    """去掉正文开头的元信息表格"""
    lines = body.split("\n")
    content_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            content_start = i + 1
            continue
        if stripped.startswith("|") or re.match(r"^\|[-:\s|]+\|$", stripped):
            content_start = i + 1
            continue
        break
    return "\n".join(lines[content_start:]).strip()


def read_document_content(
    file_path: Path,
    offset: int = 0,
    limit: int = 8000,
    strip_metadata: bool = True,
) -> dict[str, Any]:
    """读取文档内容切片

    Returns:
        content, total_chars, offset, limit, has_more, truncated, frontmatter
    """
    raw = file_path.read_text(encoding="utf-8")
    frontmatter: dict = {}
    body = raw

    if strip_metadata and raw.startswith("---"):
        frontmatter, body = parse_frontmatter(raw)
        body = strip_meta_table(body)

    total_chars = len(body)
    safe_offset = max(0, offset)
    safe_limit = max(1, limit)
    chunk = body[safe_offset : safe_offset + safe_limit]
    end = safe_offset + len(chunk)
    has_more = end < total_chars

    return {
        "content": chunk,
        "total_chars": total_chars,
        "offset": safe_offset,
        "limit": safe_limit,
        "returned_chars": len(chunk),
        "has_more": has_more,
        "truncated": has_more,
        "next_offset": end if has_more else None,
        "frontmatter": frontmatter,
    }


def format_doc_details_response(
    doc: dict,
    *,
    include_content: bool = False,
    content_info: Optional[dict] = None,
    description: str = "",
) -> str:
    """格式化 get_doc_details 输出"""
    payload: dict[str, Any] = {"document": doc}
    if description:
        payload["description"] = description
    if include_content and content_info is not None:
        payload["content"] = content_info.get("content", "")
        payload["content_meta"] = {
            "total_chars": content_info.get("total_chars", 0),
            "offset": content_info.get("offset", 0),
            "returned_chars": content_info.get("returned_chars", 0),
            "has_more": content_info.get("has_more", False),
            "next_offset": content_info.get("next_offset"),
        }
        if content_info.get("frontmatter"):
            payload["frontmatter"] = content_info["frontmatter"]
    return json.dumps(payload, ensure_ascii=False, indent=2)
