"""
知识库导航工具
供 Agent 调用，分析知识库内容
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..knowledge_base import KnowledgeBaseManager


@dataclass
class GetKBInfoTool(FunctionTool):
    """获取知识库详细信息"""

    name: str = "get_kb_info"
    description: str = "获取知识库的详细信息，包括文档数、贡献者、最近更新等"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": []
    })

    kb_manager: Optional["KnowledgeBaseManager"] = None
    book_name: str = ""

    async def run(self, event: AstrMessageEvent, **kwargs) -> str:
        """获取知识库信息"""
        if not self.kb_manager or not self.book_name:
            return "错误：工具未正确初始化"

        info = self.kb_manager.get_kb_info(self.book_name)
        if not info:
            return f"未找到知识库「{self.book_name}」"

        # 格式化输出
        lines = [
            f"知识库：{info.get('book_name', '未知')}",
            f"文档数：{info.get('doc_count', 0)} 篇",
            f"总字数：{info.get('total_words', 0)} 字",
            "",
            "贡献者：",
        ]

        for c in info.get("contributors", [])[:5]:
            author = c.get("author", "未知")
            doc_count = c.get("doc_count", 0)
            lines.append(f"  • {author} - {doc_count} 篇")

        lines.append("")
        lines.append("最近更新：")
        for u in info.get("recent_updates", [])[:5]:
            title = u.get("title", "未知")
            author = u.get("author", "")
            lines.append(f"  • 《{title}》- {author}")

        return "\n".join(lines)


@dataclass
class GetKBStructureTool(FunctionTool):
    """获取知识库目录结构"""

    name: str = "get_kb_structure"
    description: str = "获取知识库的目录结构，了解文档的组织方式和分区"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": []
    })

    kb_manager: Optional["KnowledgeBaseManager"] = None
    book_name: str = ""

    async def run(self, event: AstrMessageEvent, **kwargs) -> str:
        """获取知识库结构"""
        if not self.kb_manager or not self.book_name:
            return "错误：工具未正确初始化"

        structure = self.kb_manager.get_kb_structure(self.book_name)
        if not structure:
            return f"未找到知识库「{self.book_name}」"

        lines = [f"📚 知识库结构：{structure.get('book_name', '未知')}", ""]

        # 显示分区/文件夹
        folders = structure.get("folders", [])
        if folders:
            lines.append("📁 文档分区：")
            for folder in folders[:10]:
                name = folder.get("name", "未知")
                doc_count = folder.get("doc_count", 0)
                lines.append(f"  • {name}（{doc_count} 篇）")
            if len(folders) > 10:
                lines.append(f"  ... 共 {len(folders)} 个分区")
        else:
            lines.append("（暂无分区信息）")

        lines.append("")

        # 显示所有文档标题
        all_docs = structure.get("all_docs", [])
        if all_docs:
            lines.append("📄 所有文档：")
            for doc in all_docs[:20]:
                title = doc.get("title", "未知")
                author = doc.get("author", "")
                lines.append(f"  • 《{title}》" + (f" - {author}" if author else ""))
            if len(all_docs) > 20:
                lines.append(f"  ... 共 {len(all_docs)} 篇文档")

        return "\n".join(lines)


@dataclass
class ReadDocTool(FunctionTool):
    """读取文档详细内容"""

    name: str = "read_doc"
    description: str = "读取指定文档的详细内容。输入文档标题或标题的一部分。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "文档标题或标题的一部分"
            }
        },
        "required": ["title"]
    })

    kb_manager: Optional["KnowledgeBaseManager"] = None
    book_name: str = ""

    async def run(self, event: AstrMessageEvent, title: str = "", **kwargs) -> str:
        """读取文档内容"""
        if not self.kb_manager or not self.book_name:
            return "错误：工具未正确初始化"

        if not title:
            return "请提供文档标题"

        # 直接从文件读取完整内容
        doc = self.kb_manager.get_doc_content(self.book_name, title)
        if not doc:
            return f"未找到标题包含「{title}」的文档"

        doc_title = doc.get("title", "未知")
        author = doc.get("author", "")
        content = doc.get("content", "")
        book_name = doc.get("book_name", "")

        # 截断过长的内容
        if len(content) > 3000:
            content = content[:3000] + "\n\n... (内容已截断，共 " + str(len(content)) + " 字)"

        lines = [
            f"📖 《{doc_title}》",
            f"作者：{author}" if author else "",
            f"知识库：{book_name}" if book_name else "",
            "",
            "─" * 20,
            "",
            content if content else "（暂无内容）",
        ]

        return "\n".join(lines)


@dataclass
class SearchKBTool(FunctionTool):
    """在知识库范围内搜索"""

    name: str = "search_in_kb"
    description: str = "在当前知识库范围内搜索文档内容，返回匹配的文档片段"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词"
            }
        },
        "required": ["query"]
    })

    kb_manager: Optional["KnowledgeBaseManager"] = None
    book_name: str = ""

    async def run(self, event: AstrMessageEvent, query: str = "", **kwargs) -> str:
        """搜索知识库"""
        if not self.kb_manager or not self.book_name:
            return "错误：工具未正确初始化"

        if not query:
            return "请提供搜索关键词"

        results = self.kb_manager.search_in_kb(self.book_name, query, k=5)
        if not results:
            return f"在「{self.book_name}」中未找到相关内容"

        lines = [f"在「{self.book_name}」中找到 {len(results)} 条结果：", ""]
        for i, r in enumerate(results, 1):
            title = r.get("title", "未知")
            author = r.get("author", "")
            content = r.get("content", "")[:150]
            lines.append(f"【{i}】《{title}》" + (f" - {author}" if author else ""))
            lines.append(f"    {content}...")
            lines.append("")

        return "\n".join(lines)


# 导出所有工具
KB_GUIDE_TOOLS = [GetKBInfoTool, GetKBStructureTool, ReadDocTool, SearchKBTool]