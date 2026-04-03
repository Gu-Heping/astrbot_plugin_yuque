"""
NovaBot LLM 工具模块
"""

from .base import BaseTool
from .search import (
    SearchKnowledgeBaseTool,
    GrepLocalDocsTool,
    ReadDocTool,
    KnowledgeCardTool,
    ParseYuqueUrlTool,
)
from .metadata import SearchDocsTool, ListAuthorsTool, DocStatsTool
from .repo import ListKnowledgeBasesTool, ListRepoDocsTool
from .persona import SetPreferenceTool
from .natural_language import (
    PartnerRecommendTool,
    LearningPathTool,
    WeeklyReportTool,
    KnowledgeGapTool,
    SubscribeTool,
    UnsubscribeTool,
    ProfileViewTool,
)
from .memory_tools import (
    RecallConversationTool,
    GetSessionDetailTool,
    GetUserStatsTool,
)

__all__ = [
    "BaseTool",
    # 搜索工具
    "SearchKnowledgeBaseTool",
    "GrepLocalDocsTool",
    "ReadDocTool",
    "KnowledgeCardTool",
    "ParseYuqueUrlTool",
    # 元数据工具
    "SearchDocsTool",
    "ListAuthorsTool",
    "DocStatsTool",
    # 知识库工具
    "ListKnowledgeBasesTool",
    "ListRepoDocsTool",
    # 人格偏好工具
    "SetPreferenceTool",
    # 自然语言交互工具
    "PartnerRecommendTool",
    "LearningPathTool",
    "WeeklyReportTool",
    "KnowledgeGapTool",
    "SubscribeTool",
    "UnsubscribeTool",
    "ProfileViewTool",
    # 记忆工具
    "RecallConversationTool",
    "GetSessionDetailTool",
    "GetUserStatsTool",
]

# 所有工具类列表，用于批量注册
ALL_TOOLS = [
    SearchKnowledgeBaseTool,
    GrepLocalDocsTool,
    ReadDocTool,
    KnowledgeCardTool,
    ParseYuqueUrlTool,
    SearchDocsTool,
    ListAuthorsTool,
    DocStatsTool,
    ListKnowledgeBasesTool,
    ListRepoDocsTool,
    SetPreferenceTool,
    PartnerRecommendTool,
    LearningPathTool,
    WeeklyReportTool,
    KnowledgeGapTool,
    SubscribeTool,
    UnsubscribeTool,
    ProfileViewTool,
    # 记忆工具
    RecallConversationTool,
    GetSessionDetailTool,
    GetUserStatsTool,
]