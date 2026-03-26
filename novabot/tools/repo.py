"""
知识库相关工具：列出知识库、列出知识库文档结构
"""

import json
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .base import BaseTool


@dataclass
class ListKnowledgeBasesTool(BaseTool):
    """列出知识库工具"""

    name: str = "list_knowledge_bases"
    description: str = "列出 NOVA 社团所有语雀知识库。了解有哪些知识库可以帮助你决定去哪个知识库搜索。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": []
    })
    plugin: Any = None

    async def run(self, event):
        repos_file = self.plugin.storage.data_dir / "yuque_repos.json"
        docs_dir = self.get_docs_dir()

        # 优先从缓存的 repos 文件读取
        if repos_file.exists():
            try:
                repos = json.loads(repos_file.read_text(encoding="utf-8"))
                output = ["📚 NOVA 知识库列表:\n"]
                for repo in repos:
                    name = repo.get("name", "未知")
                    desc = repo.get("description", "") or ""
                    items = repo.get("items_count", 0)
                    output.append(f"• {name} ({items} 篇文档)")
                    if desc:
                        output.append(f"  {desc[:50]}{'...' if len(desc) > 50 else ''}")
                return "\n".join(output)
            except Exception as e:
                logger.warning(f"读取知识库列表失败: {e}")

        # 备选：从目录结构读取
        if docs_dir.exists():
            output = ["📚 NOVA 知识库列表:\n"]
            for repo_dir in sorted(docs_dir.iterdir()):
                if repo_dir.is_dir():
                    md_count = len(list(repo_dir.glob("*.md")))
                    output.append(f"• {repo_dir.name} ({md_count} 篇文档)")
            return "\n".join(output)

        return "知识库列表为空，请先执行 /sync 同步"


@dataclass
class ListRepoDocsTool(BaseTool):
    """列出知识库文档结构工具"""

    name: str = "list_repo_docs"
    description: str = "列出某个知识库下的所有文档结构（含层级）。TITLE 是分组（无内容），DOC 是实际文档。了解知识库结构后可以更有针对性地搜索。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "repo_name": {
                "type": "string",
                "description": "知识库名称，如 'astrbot搭建'、'AI Agent试水'"
            }
        },
        "required": ["repo_name"]
    })
    plugin: Any = None

    def _build_toc_tree(self, toc_list: list, parent_uuid: str = "") -> list:
        """构建 TOC 树形结构"""
        children = []
        for item in toc_list:
            if (item.get("parent_uuid") or "") == parent_uuid:
                node = {
                    "title": item.get("title", "无标题"),
                    "type": item.get("type", "DOC"),
                    "slug": item.get("slug") or item.get("url", ""),
                    "depth": item.get("depth", 1),
                }
                child_uuid = item.get("uuid", "")
                sub_children = self._build_toc_tree(toc_list, child_uuid)
                if sub_children:
                    node["children"] = sub_children
                children.append(node)
        return children

    def _format_tree(self, nodes: list, indent: str = "") -> list:
        """格式化树形结构为文本"""
        lines = []
        for node in nodes:
            title = node.get("title", "")
            doc_type = node.get("type", "DOC")
            icon = "📄" if doc_type == "DOC" else "📁"
            type_hint = "" if doc_type == "DOC" else " [分组]"
            lines.append(f"{indent}{icon} {title}{type_hint}")
            if node.get("children"):
                lines.extend(self._format_tree(node["children"], indent + "  "))
        return lines

    async def run(self, event, repo_name: str):
        docs_dir = self.get_docs_dir()
        if not docs_dir.exists():
            return "文档目录不存在，请先执行 /sync 同步"

        # 从 .repos.json 查找知识库
        repos_file = docs_dir / ".repos.json"
        matched_dir = None
        matched_repo = None

        if repos_file.exists():
            try:
                repos = json.loads(repos_file.read_text(encoding="utf-8"))
                for repo in repos:
                    name = repo.get("name", "")
                    ns = repo.get("namespace", "")
                    if repo_name.lower() in name.lower() or repo_name.lower() in ns.lower():
                        matched_repo = repo
                        matched_dir = docs_dir / ns.replace("/", "_")
                        break
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"读取知识库列表失败: {e}")

        if not matched_dir:
            # 备选：从目录名模糊匹配
            for d in docs_dir.iterdir():
                if d.is_dir() and repo_name.lower() in d.name.lower():
                    matched_dir = d
                    break

        if not matched_dir:
            available = []
            if repos_file.exists():
                try:
                    repos = json.loads(repos_file.read_text(encoding="utf-8"))
                    available = [r.get("name", "") for r in repos[:10]]
                except (json.JSONDecodeError, OSError) as e:
                    logger.debug(f"读取知识库列表失败: {e}")
            if not available:
                available = [d.name for d in docs_dir.iterdir() if d.is_dir()][:10]
            return f"未找到知识库「{repo_name}」\n可用知识库: {', '.join(available)}"

        # 读取 TOC
        toc_file = matched_dir / ".toc.json"
        if toc_file.exists():
            try:
                toc_list = json.loads(toc_file.read_text(encoding="utf-8"))
                tree = self._build_toc_tree(toc_list)
                lines = [f"📖 {matched_repo.get('name', matched_dir.name) if matched_repo else matched_dir.name} 目录结构:\n"]
                lines.extend(self._format_tree(tree))
                doc_count = sum(1 for item in toc_list if item.get("type") == "DOC")
                title_count = sum(1 for item in toc_list if item.get("type") == "TITLE")
                lines.append(f"\n共 {doc_count} 篇文档, {title_count} 个分组")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"读取 TOC 失败: {e}")

        # 备选：列出 md 文件
        md_files = list(matched_dir.glob("*.md"))
        output = [f"📖 {matched_dir.name} 文档列表:\n"]
        for md_file in sorted(md_files)[:30]:
            try:
                content = md_file.read_text(encoding="utf-8")
                title = md_file.stem
                for line in content.split("\n")[:10]:
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
                output.append(f"📄 {title}")
            except OSError as e:
                logger.debug(f"读取文件失败 {md_file}: {e}")
                output.append(f"📄 {md_file.stem}")
        if len(md_files) > 30:
            output.append(f"\n... 还有 {len(md_files) - 30} 篇文档")
        return "\n".join(output)