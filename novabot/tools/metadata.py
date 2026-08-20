"""
元数据查询工具：按作者/知识库/标题搜索、列出作者、文档统计、文档详情
"""

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..doc_utils import (
    doc_record_to_public_dict,
    format_doc_details_response,
    parse_yuque_doc_url,
    read_document_content,
)
from ..evidence import evidence_from_document_content, format_citations, format_evidence_block
from .base import BaseTool


def _api_base_url(plugin: Any) -> str:
    return getattr(plugin, "yuque_base_url", "https://www.yuque.com/api/v2")


def _format_search_doc_lines(results: list[dict], api_base_url: str) -> list[str]:
    """格式化 search_docs 单条结果"""
    lines = []
    for r in results:
        pub = doc_record_to_public_dict(r, api_base_url)
        lines.append(f"📄 {pub['title']}")
        if pub.get("yuque_id"):
            lines.append(f"   ID: {pub['yuque_id']}")
        if pub.get("slug"):
            lines.append(f"   slug: {pub['slug']}")
        if pub.get("author"):
            lines.append(f"   作者: {pub['author']}")
        if pub.get("team_name") or pub.get("team_id"):
            lines.append(f"   团队: {pub.get('team_name') or pub.get('team_id')} ({pub.get('team_id') or 'unknown'})")
        if pub.get("book_name"):
            lines.append(f"   知识库: {pub['book_name']}")
        if pub.get("created_at"):
            lines.append(f"   创建: {pub['created_at']}")
        if pub.get("updated_at"):
            lines.append(f"   更新: {pub['updated_at']}")
        if pub.get("word_count"):
            lines.append(f"   字数: {pub['word_count']}")
        if pub.get("file_path"):
            lines.append(f"   路径: {pub['file_path']}")
        if pub.get("url"):
            lines.append(f"   链接: {pub['url']}")
        lines.append("")
    return lines


def _filter_summary(**filters) -> str:
    parts = [f"{key}={value}" for key, value in filters.items() if value]
    return ", ".join(parts) if parts else "无"


@dataclass
class SearchDocsTool(BaseTool):
    """元数据搜索工具"""

    name: str = "search_docs"
    description: str = "按元数据搜索文档：作者、知识库、标题等。比 grep 更适合「查看某人的所有文档」「某知识库的最新文档」这类查询。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "author": {
                "type": "string",
                "description": "作者名（模糊匹配）"
            },
            "book": {
                "type": "string",
                "description": "知识库名（模糊匹配）"
            },
            "title": {
                "type": "string",
                "description": "文档标题（模糊匹配）"
            },
            "team_id": {
                "type": "string",
                "description": "团队 ID（可选，如 default、other）。多团队场景用来限定检索范围。"
            },
            "path_prefix": {
                "type": "string",
                "description": "本地路径前缀/片段（可选，如 other/工程 或 工程/指南）"
            },
            "updated_after": {
                "type": "string",
                "description": "更新时间下界（可选，ISO 或 YYYY-MM-DD）"
            },
            "updated_before": {
                "type": "string",
                "description": "更新时间上界（可选，ISO 或 YYYY-MM-DD）"
            },
            "order_by": {
                "type": "string",
                "description": "排序方式：updated_at（更新时间）、created_at（创建时间）、word_count（字数）",
                "default": "updated_at"
            },
            "limit": {
                "type": "integer",
                "description": "返回数量，默认 10",
                "default": 10
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        author: str = "",
        book: str = "",
        title: str = "",
        team_id: str = "",
        path_prefix: str = "",
        updated_after: str = "",
        updated_before: str = "",
        order_by: str = "updated_at",
        limit: int = 10,
    ) -> str:
        doc_index = self.get_doc_index()
        if not doc_index:
            return "元数据索引不存在，请先执行 /sync 同步"

        try:
            results = doc_index.search(
                author=author or None,
                book=book or None,
                title=title or None,
                team_id=team_id or None,
                path_prefix=path_prefix or None,
                updated_after=updated_after or None,
                updated_before=updated_before or None,
                order_by=order_by,
                limit=limit,
            )

            if not results:
                filters = []
                if author:
                    filters.append(f"作者={author}")
                if book:
                    filters.append(f"知识库={book}")
                if title:
                    filters.append(f"标题={title}")
                if team_id:
                    filters.append(f"团队={team_id}")
                if path_prefix:
                    filters.append(f"路径={path_prefix}")
                if updated_after:
                    filters.append(f"更新晚于={updated_after}")
                if updated_before:
                    filters.append(f"更新早于={updated_before}")
                return f"未找到匹配的文档（筛选: {', '.join(filters) if filters else '无'}）"

            output = [
                f"找到 {len(results)} 篇文档"
                f"（筛选: {_filter_summary(author=author, book=book, title=title, team_id=team_id, path=path_prefix, updated_after=updated_after, updated_before=updated_before)}）:\n"
            ]
            output.extend(_format_search_doc_lines(results, _api_base_url(self.plugin)))
            output.append("💡 需要完整元数据与正文时，可调用 get_doc_details(path=...)")

            return "\n".join(output)
        except Exception as e:
            return f"搜索失败: {e}"


@dataclass
class ListAuthorsTool(BaseTool):
    """列出作者工具"""

    name: str = "list_authors"
    description: str = "列出所有文档作者及其贡献统计（文档数、总字数）。适合「看看有哪些作者」「谁写的最多」这类查询。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "团队 ID（可选）"
            },
            "book": {
                "type": "string",
                "description": "知识库名（可选）"
            },
            "path_prefix": {
                "type": "string",
                "description": "路径前缀/片段（可选）"
            },
            "updated_after": {
                "type": "string",
                "description": "更新时间下界（可选）"
            },
            "updated_before": {
                "type": "string",
                "description": "更新时间上界（可选）"
            },
        },
        "required": []
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        team_id: str = "",
        book: str = "",
        path_prefix: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> str:
        doc_index = self.get_doc_index()
        if not doc_index:
            return "元数据索引不存在，请先执行 /sync 同步"

        try:
            authors = doc_index.list_authors(
                team_id=team_id or None,
                book=book or None,
                path_prefix=path_prefix or None,
                updated_after=updated_after or None,
                updated_before=updated_before or None,
            )

            if not authors:
                return f"没有找到作者信息（筛选: {_filter_summary(team_id=team_id, book=book, path=path_prefix, updated_after=updated_after, updated_before=updated_before)}）"

            output = [
                f"👥 作者列表（共 {len(authors)} 人，筛选: "
                f"{_filter_summary(team_id=team_id, book=book, path=path_prefix, updated_after=updated_after, updated_before=updated_before)}）:\n"
            ]
            for i, a in enumerate(authors[:20], 1):
                output.append(f"{i}. {a['author']}")
                output.append(f"   📄 {a['doc_count']} 篇文档, 📝 {a['total_words'] or 0} 字")
            if len(authors) > 20:
                output.append(f"\n... 还有 {len(authors) - 20} 位作者")

            return "\n".join(output)
        except Exception as e:
            return f"查询失败: {e}"


@dataclass
class DocStatsTool(BaseTool):
    """文档统计工具"""

    name: str = "doc_stats"
    description: str = "获取文档统计信息：总文档数、总字数、知识库数。可按团队、作者、知识库、路径、更新时间筛选。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "author": {
                "type": "string",
                "description": "作者名（可选，用于查看某人的统计）"
            },
            "team_id": {
                "type": "string",
                "description": "团队 ID（可选）"
            },
            "book": {
                "type": "string",
                "description": "知识库名（可选）"
            },
            "path_prefix": {
                "type": "string",
                "description": "路径前缀/片段（可选）"
            },
            "updated_after": {
                "type": "string",
                "description": "更新时间下界（可选）"
            },
            "updated_before": {
                "type": "string",
                "description": "更新时间上界（可选）"
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        author: str = "",
        team_id: str = "",
        book: str = "",
        path_prefix: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> str:
        doc_index = self.get_doc_index()
        if not doc_index:
            return "元数据索引不存在，请先执行 /sync 同步"

        try:
            stats = doc_index.get_stats(
                author=author or None,
                team_id=team_id or None,
                book=book or None,
                path_prefix=path_prefix or None,
                updated_after=updated_after or None,
                updated_before=updated_before or None,
            )

            output = [
                "📊 文档统计"
                f"（筛选: {_filter_summary(author=author, team_id=team_id, book=book, path=path_prefix, updated_after=updated_after, updated_before=updated_before)}）:\n"
            ]

            output.append(f"📄 文档数: {stats['doc_count']}")
            output.append(f"📝 总字数: {stats['total_words'] or 0}")
            output.append(f"📚 知识库数: {stats['book_count']}")

            return "\n".join(output)
        except Exception as e:
            return f"查询失败: {e}"


@dataclass
class GetDocDetailsTool(BaseTool):
    """文档详情聚合工具"""

    name: str = "get_doc_details"
    description: str = (
        "获取单篇文档的完整元数据（标题、作者、时间、字数、语雀链接等），"
        "可选附带正文。定位方式四选一：title、path、yuque_id、url。"
        "长文正文可分页：设置 content_offset 与 content_limit。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "文档标题（模糊匹配，多篇时返回候选列表）",
            },
            "path": {
                "type": "string",
                "description": "本地相对路径（search_docs 或 grep 返回的 file_path）",
            },
            "yuque_id": {
                "type": "integer",
                "description": "语雀文档 ID",
            },
            "team_id": {
                "type": "string",
                "description": "团队 ID（可选；当不同团队存在相同 yuque_id 或标题时用于限定范围）",
            },
            "url": {
                "type": "string",
                "description": "语雀文档链接",
            },
            "include_content": {
                "type": "boolean",
                "description": "是否包含正文，默认 false",
                "default": False,
            },
            "content_offset": {
                "type": "integer",
                "description": "正文起始字符偏移（分页用），默认 0",
                "default": 0,
            },
            "content_limit": {
                "type": "integer",
                "description": "正文最大返回字符数，默认 8000",
                "default": 8000,
            },
        },
        "required": [],
    })
    plugin: Any = None

    def _resolve_row(
        self,
        doc_index,
        title: str,
        path: str,
        yuque_id: Optional[int],
        team_id: str,
        url: str,
        api_base: str,
    ) -> tuple[Optional[dict], Optional[str]]:
        """解析文档记录，返回 (row, error_message)"""
        if yuque_id:
            if not team_id:
                rows = doc_index.find_docs_by_yuque_id(int(yuque_id), limit=5)
                if len(rows) > 1:
                    candidates = [doc_record_to_public_dict(r, api_base) for r in rows]
                    return None, json.dumps(
                        {
                            "error": "multiple_matches",
                            "message": f"找到 {len(rows)} 篇 yuque_id={yuque_id} 的文档，请用 team_id、path 或 url 精确指定",
                            "candidates": candidates,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                if len(rows) == 1:
                    return rows[0], None
            row = doc_index.get_doc_by_yuque_id(int(yuque_id), team_id=team_id or None)
            if not row:
                scope = f" team_id={team_id}" if team_id else ""
                return None, f"未找到 yuque_id={yuque_id}{scope} 的文档"
            return row, None

        if path:
            row = doc_index.find_doc_by_file_path(path)
            if not row:
                return None, f"未找到路径为「{path}」的文档"
            if team_id and row.get("team_id") != team_id:
                return None, f"路径「{path}」存在，但不属于 team_id={team_id}"
            return row, None

        if url:
            parsed = parse_yuque_doc_url(url)
            if not parsed:
                return None, f"无法解析语雀链接: {url}"
            namespace, doc_slug = parsed
            rows = doc_index.find_docs_by_slug(doc_slug, limit=5)
            if not rows:
                return None, f"未找到 slug 为「{doc_slug}」的文档"
            if team_id:
                rows = [row_dict for row_dict in rows if row_dict.get("team_id") == team_id]
                if not rows:
                    return None, f"未找到 slug 为「{doc_slug}」且 team_id={team_id} 的文档"
            matched = None
            for row_dict in rows:
                book_ns = row_dict.get("book_namespace", "")
                if namespace in book_ns or book_ns in namespace:
                    matched = row_dict
                    break
            if not matched:
                matched = rows[0]
            return matched, None

        if title:
            rows = doc_index.search(title=title, team_id=team_id or None, limit=5)
            if not rows:
                scope = f"（team_id={team_id}）" if team_id else ""
                return None, f"未找到标题包含「{title}」的文档{scope}"
            if len(rows) > 1:
                candidates = [doc_record_to_public_dict(r, api_base) for r in rows]
                return None, json.dumps(
                    {
                        "error": "multiple_matches",
                        "message": f"找到 {len(rows)} 篇匹配文档，请用 path 或 yuque_id 精确指定",
                        "candidates": candidates,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            return rows[0], None

        return None, "请提供 title、path、yuque_id 或 url 之一"

    async def run(
        self,
        event: AstrMessageEvent,
        title: str = "",
        path: str = "",
        yuque_id: int = 0,
        team_id: str = "",
        url: str = "",
        include_content: bool = False,
        content_offset: int = 0,
        content_limit: int = 8000,
    ) -> str:
        doc_index = self.get_doc_index()
        if not doc_index:
            return "元数据索引不存在，请先执行 /sync 同步"

        api_base = _api_base_url(self.plugin)
        yuque_id_val = int(yuque_id) if yuque_id else None

        try:
            row, err = self._resolve_row(
                doc_index,
                title.strip(),
                path.strip(),
                yuque_id_val,
                team_id.strip(),
                url.strip(),
                api_base,
            )
            if err:
                return err
            if not row:
                return "未找到文档"

            pub = doc_record_to_public_dict(row, api_base)
            content_info = None

            if include_content:
                file_path = row.get("file_path")
                if not file_path:
                    return format_doc_details_response(
                        pub,
                        include_content=False,
                        description="文档无本地文件路径，无法读取正文",
                    )
                doc_file = self.get_docs_dir() / file_path
                if not doc_file.exists():
                    return format_doc_details_response(
                        pub,
                        include_content=False,
                        description=f"本地文件不存在: {file_path}",
                    )
                try:
                    content_info = read_document_content(
                        doc_file,
                        offset=content_offset,
                        limit=min(max(content_limit, 1), 20000),
                        strip_metadata=True,
                    )
                except OSError as e:
                    logger.error(f"[get_doc_details] 读取文件失败: {e}")
                    return format_doc_details_response(
                        pub,
                        include_content=False,
                        description=f"读取正文失败: {e}",
                    )

            response = format_doc_details_response(
                pub,
                include_content=include_content,
                content_info=content_info,
            )
            if include_content and content_info is not None:
                evidence = [
                    evidence_from_document_content(
                        {**row, "url": pub.get("url") or ""},
                        content_info.get("content", ""),
                        evidence_id="E1",
                        evidence_type="get_doc_details",
                    )
                ]
                return "\n\n".join([format_evidence_block(evidence), format_citations(evidence), response])
            return response
        except Exception as e:
            logger.error(f"[get_doc_details] 失败: {e}", exc_info=True)
            return f"获取文档详情失败: {e}"
