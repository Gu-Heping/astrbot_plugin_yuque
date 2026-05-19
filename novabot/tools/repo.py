"""
知识库相关工具：列出知识库、列出知识库文档结构
"""

import json
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

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

    async def run(self, event: AstrMessageEvent) -> str:
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

    def _build_slug_path_maps(self, doc_index, book_name: str) -> tuple[dict, dict]:
        """从索引构建 slug/title -> file_path 映射"""
        slug_map: dict[str, str] = {}
        title_map: dict[str, dict] = {}
        if not doc_index or not book_name:
            return slug_map, title_map
        try:
            docs = doc_index.search(book=book_name, limit=500)
            for doc in docs:
                slug = doc.get("slug", "")
                title = doc.get("title", "")
                fp = doc.get("file_path", "")
                author = doc.get("author", "")
                if slug and fp:
                    slug_map[slug] = fp
                if title:
                    title_map[title] = {"slug": slug, "file_path": fp, "author": author}
        except Exception as e:
            logger.debug(f"构建 slug/path 映射失败: {e}")
        return slug_map, title_map

    def _build_toc_tree(
        self,
        toc_list: list,
        parent_uuid: str = "",
        slug_map: dict | None = None,
        title_map: dict | None = None,
    ) -> list:
        """构建 TOC 树形结构"""
        slug_map = slug_map or {}
        title_map = title_map or {}
        children = []
        for item in toc_list:
            if (item.get("parent_uuid") or "") == parent_uuid:
                title = item.get("title", "无标题")
                doc_type = item.get("type", "DOC")
                slug = item.get("slug") or item.get("url", "") or ""
                file_path = ""
                if doc_type == "DOC":
                    file_path = slug_map.get(slug, "")
                    if not file_path and title in title_map:
                        file_path = title_map[title].get("file_path", "")
                        if not slug:
                            slug = title_map[title].get("slug", "")
                node = {
                    "title": title,
                    "type": doc_type,
                    "slug": slug,
                    "file_path": file_path,
                    "depth": item.get("depth", 1),
                }
                child_uuid = item.get("uuid", "")
                sub_children = self._build_toc_tree(
                    toc_list, child_uuid, slug_map, title_map
                )
                if sub_children:
                    node["children"] = sub_children
                children.append(node)
        return children

    def _format_tree(self, nodes: list, author_map: dict = None, indent: str = "") -> list:
        """格式化树形结构为文本"""
        if author_map is None:
            author_map = {}
        lines = []
        for node in nodes:
            title = node.get("title", "")
            doc_type = node.get("type", "DOC")
            icon = "📄" if doc_type == "DOC" else "📁"
            type_hint = "" if doc_type == "DOC" else " [分组]"
            author_hint = ""
            if doc_type == "DOC" and author_map.get(title):
                author_hint = f" (by {author_map[title]})"
            path_hint = ""
            if doc_type == "DOC":
                parts = []
                slug = node.get("slug", "")
                fp = node.get("file_path", "")
                if slug:
                    parts.append(f"slug={slug}")
                if fp:
                    parts.append(f"path={fp}")
                if parts:
                    path_hint = f" [{', '.join(parts)}]"
            lines.append(f"{indent}{icon} {title}{type_hint}{author_hint}{path_hint}")
            if node.get("children"):
                lines.extend(self._format_tree(node["children"], author_map, indent + "  "))
        return lines

    async def run(self, event: AstrMessageEvent, repo_name: str) -> str:
        docs_dir = self.get_docs_dir()
        if not docs_dir.exists():
            return "文档目录不存在，请先执行 /sync 同步"

        # 从 .repos.json 查找知识库
        repos_file = docs_dir / ".repos.json"
        matched_dir = None
        matched_repo = None
        matched_namespace = None

        if repos_file.exists():
            try:
                repos = json.loads(repos_file.read_text(encoding="utf-8"))
                for repo in repos:
                    name = repo.get("name", "")
                    ns = repo.get("namespace", "")
                    if repo_name.lower() in name.lower() or repo_name.lower() in ns.lower():
                        matched_repo = repo
                        matched_namespace = ns
                        matched_dir = docs_dir / self._namespace_to_dirname(ns, name)
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

        # 准备作者与 slug/path 映射（从 SQLite 索引）
        author_map = {}
        slug_map = {}
        title_map = {}
        doc_index = self.get_doc_index()
        book_name = matched_repo.get("name", "") if matched_repo else repo_name
        if doc_index:
            try:
                slug_map, title_map = self._build_slug_path_maps(doc_index, book_name)
                for title, info in title_map.items():
                    if info.get("author"):
                        author_map[title] = info["author"]
            except Exception as e:
                logger.debug(f"从 SQLite 读取文档映射失败: {e}")

        # 从 TOC 读取层级结构
        toc_file = matched_dir / ".toc.json"
        if toc_file.exists():
            try:
                toc_list = json.loads(toc_file.read_text(encoding="utf-8"))
                tree = self._build_toc_tree(toc_list, slug_map=slug_map, title_map=title_map)
                lines = [f"📖 {matched_repo.get('name', matched_dir.name) if matched_repo else matched_dir.name} 目录结构:\n"]
                lines.extend(self._format_tree(tree, author_map))
                doc_count = sum(1 for item in toc_list if item.get("type") == "DOC")
                title_count = sum(1 for item in toc_list if item.get("type") == "TITLE")
                lines.append(f"\n共 {doc_count} 篇文档, {title_count} 个分组")
                lines.append("💡 DOC 节点后的 path 可用于 read_doc 或 get_doc_details")
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"读取 TOC 失败: {e}")

        # 最后备选：列出 md 文件
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

    def _namespace_to_dirname(self, namespace: str, repo_name: str) -> str:
        """将 namespace 转换为目录名"""
        if repo_name:
            return self.slug_safe(repo_name)
        return namespace.replace("/", "_")