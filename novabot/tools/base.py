"""
NovaBot 工具基类
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..doc_index import DocIndex


@dataclass
class BaseTool(FunctionTool):
    """工具基类，提供公共方法"""

    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, **kwargs) -> str:
        """工具执行方法，子类需要重写

        Args:
            event: 消息事件对象
            **kwargs: 工具参数

        Returns:
            工具执行结果字符串
        """
        raise NotImplementedError("Subclasses must implement run()")

    def get_docs_dir(self) -> Path:
        """获取文档目录"""
        return self.plugin.storage.docs_dir

    def get_db_path(self) -> Path:
        """获取元数据索引数据库路径"""
        return self.plugin.storage.data_dir / "doc_index.db"

    def get_doc_index(self) -> Optional["DocIndex"]:
        """获取文档索引实例

        Returns:
            DocIndex 实例，如果数据库不存在则返回 None
        """
        db_path = self.get_db_path()
        if not db_path.exists():
            return None

        from ..doc_index import DocIndex
        return DocIndex(str(db_path))

    @staticmethod
    def slug_safe(s: str) -> str:
        """安全文件名（与 YuqueClient.slug_safe 一致）"""
        for c in r'/\:*?"<>|':
            s = s.replace(c, "_")
        return s.strip() or "untitled"