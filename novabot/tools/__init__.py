"""
NovaBot LLM 工具模块
"""

from .base import BaseTool
from .search import SearchKnowledgeBaseTool, GrepLocalDocsTool, ReadDocTool
from .metadata import SearchDocsTool, ListAuthorsTool, DocStatsTool
from .repo import ListKnowledgeBasesTool, ListRepoDocsTool

__all__ = [
    "BaseTool",
    # 搜索工具
    "SearchKnowledgeBaseTool",
    "GrepLocalDocsTool",
    "ReadDocTool",
    # 元数据工具
    "SearchDocsTool",
    "ListAuthorsTool",
    "DocStatsTool",
    # 知识库工具
    "ListKnowledgeBasesTool",
    "ListRepoDocsTool",
]

# 所有工具类列表，用于批量注册
ALL_TOOLS = [
    SearchKnowledgeBaseTool,
    GrepLocalDocsTool,
    ReadDocTool,
    SearchDocsTool,
    ListAuthorsTool,
    DocStatsTool,
    ListKnowledgeBasesTool,
    ListRepoDocsTool,
]