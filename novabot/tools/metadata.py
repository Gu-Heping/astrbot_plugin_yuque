"""
元数据查询工具：按作者/知识库/标题搜索、列出作者、文档统计
"""

from dataclasses import dataclass, field
from typing import Any

from .base import BaseTool


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

    async def run(self, event, author: str = "", book: str = "", title: str = "", order_by: str = "updated_at", limit: int = 10):
        doc_index = self.get_doc_index()
        if not doc_index:
            return "元数据索引不存在，请先执行 /sync 同步"

        try:
            results = doc_index.search(
                author=author or None,
                book=book or None,
                title=title or None,
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
                return f"未找到匹配的文档（筛选: {', '.join(filters) if filters else '无'}）"

            output = [f"找到 {len(results)} 篇文档:\n"]
            for r in results:
                output.append(f"📄 {r['title']}")
                if r.get('author'):
                    output.append(f"   作者: {r['author']}")
                if r.get('book_name'):
                    output.append(f"   知识库: {r['book_name']}")
                if r.get('updated_at'):
                    output.append(f"   更新: {r['updated_at'][:10]}")
                if r.get('word_count'):
                    output.append(f"   字数: {r['word_count']}")
                if r.get('file_path'):
                    output.append(f"   路径: {r['file_path']}")
                output.append("")

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
        "properties": {},
        "required": []
    })
    plugin: Any = None

    async def run(self, event):
        doc_index = self.get_doc_index()
        if not doc_index:
            return "元数据索引不存在，请先执行 /sync 同步"

        try:
            authors = doc_index.list_authors()

            if not authors:
                return "没有找到作者信息"

            output = [f"👥 作者列表（共 {len(authors)} 人）:\n"]
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
    description: str = "获取文档统计信息：总文档数、总字数、知识库数。可按作者筛选。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "author": {
                "type": "string",
                "description": "作者名（可选，用于查看某人的统计）"
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(self, event, author: str = ""):
        doc_index = self.get_doc_index()
        if not doc_index:
            return "元数据索引不存在，请先执行 /sync 同步"

        try:
            stats = doc_index.get_stats(author=author or None)

            if author:
                output = [f"📊 {author} 的贡献统计:\n"]
            else:
                output = ["📊 NOVA 知识库统计:\n"]

            output.append(f"📄 文档数: {stats['doc_count']}")
            output.append(f"📝 总字数: {stats['total_words'] or 0}")
            output.append(f"📚 知识库数: {stats['book_count']}")

            return "\n".join(output)
        except Exception as e:
            return f"查询失败: {e}"