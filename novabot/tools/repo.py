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
class ListTeamsTool(BaseTool):
    """列出可检索团队工具"""

    name: str = "list_teams"
    description: str = "列出可检索语雀团队及其说明、同步状态和 team_id。用于在知识事实问答前决定检索范围。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent) -> str:
        teams = self._teams_from_registry()
        repos_by_team = self._repos_by_team()
        sync_state = self._sync_state()

        if not teams and repos_by_team:
            teams = [
                {
                    "team_id": team_id,
                    "name": repos[0].get("team_name") or team_id,
                    "description": "",
                    "enabled": True,
                }
                for team_id, repos in repos_by_team.items()
            ]

        if not teams:
            return "尚未配置或同步任何语雀团队。请先配置 yuque_token / yuque_teams 并执行 /sync。"

        lines = ["👥 可检索团队列表:\n"]
        for team in teams:
            team_id = team["team_id"]
            repos = repos_by_team.get(team_id, [])
            team_state = (sync_state.get("teams") or {}).get(team_id, {})
            lines.append(
                f"• {team['name']} (team_id={team_id}, "
                f"{'enabled' if team.get('enabled', True) else 'disabled'})"
            )
            if team.get("description"):
                lines.append(f"  描述: {team['description']}")
            lines.append(
                "  同步: "
                f"{team_state.get('repos_count', len(repos))} 个知识库, "
                f"{team_state.get('docs_count', 0)} 篇文档"
            )
            if repos:
                repo_names = [repo.get("name", "") for repo in repos[:5] if repo.get("name")]
                lines.append(f"  知识库: {', '.join(repo_names)}")
            lines.append(f"  scope: team_id={team_id}")
        lines.append("\n💡 可继续调用 list_knowledge_bases(team_id=...) 查看该团队知识库。")
        return "\n".join(lines)

    def _teams_from_registry(self) -> list[dict]:
        registry = getattr(self.plugin, "team_registry", None)
        if not registry:
            return []
        list_enabled = getattr(registry, "list_enabled", None)
        if not callable(list_enabled):
            return []
        return [
            {
                "team_id": str(getattr(team, "team_id", "default")),
                "name": str(getattr(team, "name", "") or getattr(team, "team_id", "default")),
                "description": str(getattr(team, "description", "") or ""),
                "enabled": bool(getattr(team, "enabled", True)),
            }
            for team in list_enabled()
        ]

    def _repos_by_team(self) -> dict[str, list[dict]]:
        repos_file = self.plugin.storage.data_dir / "yuque_repos.json"
        if not repos_file.exists():
            return {}
        try:
            repos = json.loads(repos_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取知识库列表失败: {e}")
            return {}
        grouped: dict[str, list[dict]] = {}
        for repo in repos if isinstance(repos, list) else []:
            grouped.setdefault(str(repo.get("team_id") or "default"), []).append(repo)
        return grouped

    def _sync_state(self) -> dict:
        loader = getattr(self.plugin.storage, "load_sync_state", None)
        if not callable(loader):
            return {}
        try:
            return loader() or {}
        except Exception as e:
            logger.debug(f"读取同步状态失败: {e}")
            return {}


@dataclass
class ListKnowledgeBasesTool(BaseTool):
    """列出知识库工具"""

    name: str = "list_knowledge_bases"
    description: str = "列出 NOVA 社团所有语雀团队与知识库。用于决定 search/grep/read_docs 的 team_id 和 repository 范围。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "团队 ID 过滤（可选）。不确定时留空以查看所有团队。"
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, team_id: str = "") -> str:
        repos_file = self.plugin.storage.data_dir / "yuque_repos.json"
        docs_dir = self.get_docs_dir()

        # 优先从缓存的 repos 文件读取
        if repos_file.exists():
            try:
                repos = json.loads(repos_file.read_text(encoding="utf-8"))
                repos = [
                    repo for repo in repos if not team_id or str(repo.get("team_id") or "default") == team_id
                ]
                if not repos:
                    return f"未找到 team_id={team_id} 的知识库，请先执行 /sync 同步或放宽团队范围"
                return _format_repos_for_scope(repos, team_id=team_id)
            except Exception as e:
                logger.warning(f"读取知识库列表失败: {e}")

        # 备选：从目录结构读取
        if docs_dir.exists():
            output = ["📚 NOVA 知识库列表（目录推断）:\n"]
            for repo_team_id, repo_dir in _iter_repo_dirs_from_fs(docs_dir, team_id=team_id):
                md_count = len(list(repo_dir.glob("*.md")))
                rel_path = repo_dir.relative_to(docs_dir).as_posix()
                output.append(f"• {repo_dir.name} ({md_count} 篇文档)")
                output.append(f"  scope: team_id={repo_team_id}, path_prefix={rel_path}")
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
            },
            "team_id": {
                "type": "string",
                "description": "团队 ID（可选）。多团队同名知识库时用于精确选择。"
            }
        },
        "required": ["repo_name"]
    })
    plugin: Any = None

    def _build_slug_path_maps(self, doc_index, book_name: str, team_id: str = "") -> tuple[dict, dict]:
        """从索引构建 slug/title -> file_path 映射"""
        slug_map: dict[str, str] = {}
        title_map: dict[str, dict] = {}
        if not doc_index or not book_name:
            return slug_map, title_map
        try:
            docs = doc_index.search(book=book_name, team_id=team_id or None, limit=500)
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

    async def run(self, event: AstrMessageEvent, repo_name: str, team_id: str = "") -> str:
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
                cache_matches = []
                for repo in repos:
                    repo_team_id = str(repo.get("team_id") or "default")
                    if team_id and repo_team_id != team_id:
                        continue
                    name = repo.get("name", "")
                    ns = repo.get("namespace", "")
                    if repo_name.lower() in name.lower() or repo_name.lower() in ns.lower():
                        cache_matches.append(repo)
                if len(cache_matches) > 1 and not team_id:
                    choices = [_repo_scope_label(r) for r in cache_matches[:10]]
                    return f"找到多个知识库「{repo_name}」，请指定 team_id:\n" + "\n".join(choices)
                if cache_matches:
                    matched_repo = cache_matches[0]
                    matched_dir = docs_dir / self._repo_dir_for_repo(matched_repo)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"读取知识库列表失败: {e}")

        if not matched_dir:
            # 备选：从目录名模糊匹配
            matches = [
                (repo_team_id, repo_dir)
                for repo_team_id, repo_dir in _iter_repo_dirs_from_fs(docs_dir, team_id=team_id)
                if repo_name.lower() in repo_dir.name.lower()
            ]
            if len(matches) > 1 and not team_id:
                choices = [
                    f"{repo_dir.name} (team_id={repo_team_id}, path_prefix={repo_dir.relative_to(docs_dir).as_posix()})"
                    for repo_team_id, repo_dir in matches[:10]
                ]
                return f"找到多个知识库「{repo_name}」，请指定 team_id:\n" + "\n".join(choices)
            if matches:
                resolved_team_id, matched_dir = matches[0]
                matched_repo = {
                    "name": matched_dir.name,
                    "team_id": resolved_team_id,
                    "namespace": "",
                }

        if not matched_dir:
            available = []
            if repos_file.exists():
                try:
                    repos = json.loads(repos_file.read_text(encoding="utf-8"))
                    available = [
                        _repo_scope_label(r)
                        for r in repos
                        if not team_id or str(r.get("team_id") or "default") == team_id
                    ][:10]
                except (json.JSONDecodeError, OSError) as e:
                    logger.debug(f"读取知识库列表失败: {e}")
            if not available:
                available = [
                    f"{repo_dir.name} (team_id={repo_team_id})"
                    for repo_team_id, repo_dir in _iter_repo_dirs_from_fs(docs_dir, team_id=team_id)
                ][:10]
            return f"未找到知识库「{repo_name}」\n可用知识库: {', '.join(available)}"

        # 准备作者与 slug/path 映射（从 SQLite 索引）
        author_map = {}
        slug_map = {}
        title_map = {}
        doc_index = self.get_doc_index()
        book_name = matched_repo.get("name", "") if matched_repo else repo_name
        resolved_team_id = str(matched_repo.get("team_id") or team_id or "") if matched_repo else team_id
        if doc_index:
            try:
                slug_map, title_map = self._build_slug_path_maps(
                    doc_index, book_name, team_id=resolved_team_id
                )
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
                title = matched_repo.get("name", matched_dir.name) if matched_repo else matched_dir.name
                lines = [f"📖 {title} 目录结构:\n"]
                if matched_repo:
                    lines.extend(_repo_scope_lines(matched_repo))
                    lines.append("")
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

    def _repo_dir_for_repo(self, repo: dict) -> Any:
        team_id = str(repo.get("team_id") or "default")
        base = self.get_docs_dir()
        if team_id != "default":
            base = base / team_id
        return base / self._namespace_to_dirname(repo.get("namespace", ""), repo.get("name", ""))


def _format_repos_for_scope(repos: list[dict], team_id: str = "") -> str:
    by_team: dict[str, list[dict]] = {}
    for repo in repos:
        by_team.setdefault(str(repo.get("team_id") or "default"), []).append(repo)

    title = "📚 NOVA 知识库列表"
    if team_id:
        title += f"（team_id={team_id}）"
    output = [f"{title}:\n"]
    for current_team_id, team_repos in by_team.items():
        team_name = team_repos[0].get("team_name") or current_team_id
        output.append(f"团队: {team_name} (team_id={current_team_id})")
        for repo in team_repos:
            output.append(f"• {_repo_scope_label(repo)}")
            output.extend(f"  {line}" for line in _repo_scope_lines(repo))
            desc = repo.get("description", "") or ""
            if desc:
                output.append(f"  描述: {desc[:80]}{'...' if len(desc) > 80 else ''}")
        output.append("")
    output.append("💡 检索时可将 team_id 与 repository 一起传给 search_knowledge_base / grep_local_docs。")
    return "\n".join(output).strip()


def _repo_scope_label(repo: dict) -> str:
    name = repo.get("name", "未知")
    items = repo.get("items_count", 0)
    team_id = repo.get("team_id") or "default"
    return f"{name} ({items} 篇文档, team_id={team_id})"


def _repo_scope_lines(repo: dict) -> list[str]:
    lines = [
        f"scope: team_id={repo.get('team_id') or 'default'}, repository={repo.get('name', '')}",
    ]
    namespace = repo.get("namespace") or ""
    if namespace:
        lines.append(f"namespace: {namespace}")
    return lines


def _iter_repo_dirs_from_fs(docs_dir, team_id: str = ""):
    for root_dir in sorted(docs_dir.iterdir()):
        if not root_dir.is_dir():
            continue

        if team_id and team_id != "default":
            if root_dir.name != team_id:
                continue
            for nested in sorted(root_dir.iterdir()):
                if nested.is_dir() and _looks_like_repo_dir(nested):
                    yield team_id, nested
            continue

        if _looks_like_repo_dir(root_dir):
            yield "default", root_dir
            continue

        if not team_id:
            for nested in sorted(root_dir.iterdir()):
                if nested.is_dir() and _looks_like_repo_dir(nested):
                    yield root_dir.name, nested


def _looks_like_repo_dir(path) -> bool:
    return (path / ".toc.json").exists() or any(path.glob("*.md"))
