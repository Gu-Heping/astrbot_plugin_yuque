"""
NovaBot 工具基类
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import FunctionTool


@dataclass
class BaseTool(FunctionTool):
    """工具基类，提供公共方法"""

    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)
    plugin: Any = None

    def get_docs_dir(self) -> Path:
        """获取文档目录"""
        return self.plugin.storage.data_dir / "yuque_docs"

    def get_db_path(self) -> Path:
        """获取元数据索引数据库路径"""
        return self.plugin.storage.data_dir / "doc_index.db"