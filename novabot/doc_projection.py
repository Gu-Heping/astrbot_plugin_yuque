"""
NovaBot 文档投影模块
统一构建 Markdown 与索引元数据，供全量同步和 Webhook 增量更新复用。
"""

import re
from typing import Dict, Optional

import yaml

from .yuque_client import YuqueClient


def count_chinese_words(text: str) -> int:
    """统计中文字数（过滤空白字符和 Markdown 语法字符）"""
    if not text:
        return 0

    cleaned = re.sub(r"```[\s\S]*?```", "", text)
    cleaned = re.sub(r"`[^`]+`", "", cleaned)
    cleaned = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\(.*?\)", r"\1", cleaned)
    cleaned = re.sub(r"\*\*|\*|__|_(?=\w)", "", cleaned)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^>\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^[\-\*\+]\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\d+\.\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"~~", "", cleaned)
    cleaned = re.sub(r"\|", "", cleaned)
    cleaned = re.sub(r"^[-\*]{3,}\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", "", cleaned)
    return len(cleaned)


def build_markdown(detail: Dict, author: str = "") -> str:
    """构建标准 Markdown 输出（frontmatter + 元信息表 + 正文）"""
    book = detail.get("book", {})
    fm = {
        "id": detail.get("id"),
        "title": detail.get("title", ""),
        "slug": detail.get("slug", ""),
        "created_at": YuqueClient.normalize_timestamp(detail.get("created_at")),
        "updated_at": YuqueClient.normalize_timestamp(detail.get("updated_at")),
    }
    if author:
        fm["author"] = author
    if book.get("name"):
        fm["book_name"] = book["name"]
    if detail.get("description"):
        fm["description"] = detail["description"]

    creator_id = detail.get("user_id") or detail.get("creator_id")
    if creator_id:
        fm["creator_id"] = creator_id

    yaml_block = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    body = detail.get("body", "") or detail.get("content", "") or ""
    meta_table = (
        "| 作者 | 创建时间 | 更新时间 |\n"
        "|------|----------|----------|\n"
        f"| {author or '未知'} | {fm['created_at']} | {fm['updated_at']} |\n\n"
    )
    return f"---\n{yaml_block}\n---\n\n{meta_table}{body}"


def build_doc_metadata(
    detail: Dict,
    rel_path: str,
    author: str = "",
    fallback_title: str = "",
    fallback_slug: str = "",
    fallback_namespace: Optional[str] = None,
) -> Dict:
    """构建 SQLite/RAG 使用的文档元数据字典"""
    book = detail.get("book", {})
    body = detail.get("body", "") or detail.get("content", "") or ""
    creator_id = (
        detail.get("user_id")
        or (detail.get("creator") or {}).get("id")
        or (detail.get("user") or {}).get("id")
        or detail.get("creator_id")
    )

    return {
        "yuque_id": detail.get("id"),
        "title": detail.get("title", fallback_title),
        "slug": detail.get("slug", fallback_slug),
        "author": author,
        "book_name": book.get("name", "") if book else "",
        "book_namespace": (
            book.get("namespace", "") if book else ""
        ) or fallback_namespace or "",
        "creator_id": creator_id,
        "created_at": YuqueClient.normalize_timestamp(detail.get("created_at")),
        "updated_at": YuqueClient.normalize_timestamp(detail.get("updated_at")),
        "word_count": count_chinese_words(body),
        "file_path": rel_path,
    }
