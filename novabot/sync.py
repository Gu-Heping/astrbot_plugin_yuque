"""
NovaBot 文档同步模块
基于 yuque2git 实现，支持 TOC 层级处理、孤儿文件清理
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from astrbot.api import logger

from .yuque_client import YuqueClient


def toc_list_children(parent_uuid: Optional[str], toc_by_uuid: Dict[str, Dict]) -> List[Dict]:
    """返回父节点下的子节点列表（按 child_uuid/sibling_uuid 链表顺序）"""
    out: List[Dict] = []

    if parent_uuid is None:
        # 根节点：parent_uuid 为空或 None
        roots = [n for n in toc_by_uuid.values() if n.get("parent_uuid") in (None, "")]
        if not roots:
            return out

        # 找到第一个根节点（没有 sibling_uuid 指向它的）
        sibling_targets = {n.get("sibling_uuid") for n in roots if n.get("sibling_uuid")}
        first_uuid = None
        for n in roots:
            u = n.get("uuid")
            if u and u not in sibling_targets:
                first_uuid = u
                break

        if first_uuid and first_uuid in toc_by_uuid:
            node = toc_by_uuid[first_uuid]
            while node:
                out.append(node)
                sibling_uuid = node.get("sibling_uuid")
                node = toc_by_uuid.get(sibling_uuid) if sibling_uuid else None

        # 如果链表遍历不完整，回退到列表
        if len(out) != len(roots):
            out = roots
    else:
        # 子节点：从 parent.child_uuid 开始遍历
        parent = toc_by_uuid.get(parent_uuid)
        start_uuid = parent.get("child_uuid") if parent else None

        if start_uuid and start_uuid in toc_by_uuid:
            node = toc_by_uuid[start_uuid]
            while node:
                if (node.get("parent_uuid") or "") == parent_uuid:
                    out.append(node)
                sibling_uuid = node.get("sibling_uuid")
                node = toc_by_uuid.get(sibling_uuid) if sibling_uuid else None

        # 如果链表遍历失败，回退到过滤
        if not out:
            out = [n for n in toc_by_uuid.values() if (n.get("parent_uuid") or "") == parent_uuid]

    return out


class DocSyncer:
    """文档同步器"""

    def __init__(
        self,
        client: YuqueClient,
        output_dir: Path,
        members: Optional[Dict[str, Dict]] = None,
        global_index: Optional[Dict[str, str]] = None,
    ):
        self.client = client
        self.output_dir = output_dir
        self.members = members or {}
        self.used_basenames: Dict[tuple, set] = {}  # (repo_name, parent_path) -> set of basenames
        self.global_index = global_index or {}  # yuque_id -> path (跨知识库)
        self.doc_metadata: List[Dict] = []  # 收集文档元数据

    async def sync_repo(self, namespace: str, repo_name: str) -> Dict:
        """同步单个知识库

        Args:
            namespace: 知识库命名空间
            repo_name: 知识库名称

        Returns:
            同步统计信息
        """
        # 确保目录名不为空
        dir_name = YuqueClient.slug_safe(repo_name) or namespace.replace("/", "_")
        repo_dir = self.output_dir / dir_name
        repo_dir.mkdir(parents=True, exist_ok=True)

        # 获取 TOC
        toc_list = await self.client.get_repo_toc(namespace)
        toc_by_uuid = {n["uuid"]: n for n in toc_list if n.get("uuid")}

        # 保存 TOC
        toc_file = repo_dir / ".toc.json"
        toc_file.write_text(json.dumps(toc_list, ensure_ascii=False, indent=2), encoding="utf-8")

        # 处理 TOC 节点
        roots = toc_list_children(None, toc_by_uuid)
        stats = {"docs": 0, "titles": 0, "errors": 0, "removed": 0}
        repo_index = {}  # 本仓库的 yuque_id -> path

        for item in roots:
            await self._process_toc_item(
                namespace, repo_name, repo_dir, item, "", toc_by_uuid, repo_index, stats
            )

        # 保存本仓库索引
        if repo_index:
            index_file = repo_dir / ".index.json"
            index_file.write_text(json.dumps(repo_index, ensure_ascii=False, indent=2), encoding="utf-8")

        # 更新全局索引
        self.global_index.update(repo_index)

        # 清理孤儿文件：删除不在当前 TOC 中的 .md 文件
        valid_paths: Set[str] = set(repo_index.values())
        for md_file in repo_dir.rglob("*.md"):
            rel_path = str(md_file.relative_to(self.output_dir))
            if rel_path not in valid_paths:
                try:
                    md_file.unlink()
                    stats["removed"] += 1
                    logger.info(f"[Sync] 删除孤儿文件: {md_file.relative_to(self.output_dir)}")
                except OSError as e:
                    logger.warning(f"[Sync] 删除文件失败: {e}")

        # 清理空目录（只含 .toc.json 的目录）
        self._cleanup_empty_dirs(repo_dir)

        logger.info(f"[Sync] {repo_name}: {stats['docs']} docs, {stats['titles']} titles, {stats['removed']} removed")
        return stats

    def _cleanup_empty_dirs(self, repo_dir: Path) -> None:
        """清理只含 .toc.json 的空目录"""
        for _ in range(10):  # 多轮清理，因为子目录删除后父目录可能也变空
            removed_any = False
            for d in sorted(repo_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if not d.is_dir() or d == repo_dir:
                    continue
                items = list(d.iterdir())
                # 只含 .toc.json 的目录
                if len(items) == 1 and items[0].name == ".toc.json" and items[0].is_file():
                    try:
                        items[0].unlink()
                        d.rmdir()
                        logger.debug(f"[Sync] 清理空目录: {d.relative_to(self.output_dir)}")
                        removed_any = True
                    except OSError:
                        pass
            if not removed_any:
                break

    async def _process_toc_item(
        self,
        namespace: str,
        repo_name: str,
        repo_dir: Path,
        toc_item: Dict,
        parent_path: str,
        toc_by_uuid: Dict[str, Dict],
        repo_index: Dict[str, str],
        stats: Dict[str, int],
    ) -> None:
        """递归处理 TOC 节点"""
        doc_type = toc_item.get("type", "DOC")
        title = toc_item.get("title", "无标题")
        slug = toc_item.get("url") or toc_item.get("slug") or toc_item.get("uuid", "")
        yuque_id = toc_item.get("id")
        uuid = toc_item.get("uuid", "")

        if doc_type in ("DOC", "SHEET") and slug:
            # 文档：获取详情并写入 Markdown
            try:
                detail = await self.client.get_doc_detail(namespace, slug)
                if not detail:
                    logger.warning(f"[Sync] 跳过文档（无详情）: {title}")
                    stats["errors"] += 1
                    return

                # 作者名
                author = self._resolve_author(detail)

                # 写入 Markdown
                base = self._resolve_basename(repo_name, parent_path, YuqueClient.doc_basename(title, slug))
                if parent_path:
                    out_file = repo_dir / parent_path / f"{base}.md"
                else:
                    out_file = repo_dir / f"{base}.md"

                out_file.parent.mkdir(parents=True, exist_ok=True)

                # 检查是否移动了位置（文档ID对应旧路径，但新路径不同）
                rel_path = str(out_file.relative_to(self.output_dir))
                if yuque_id:
                    yuque_id_str = str(yuque_id)
                    old_path = self.global_index.get(yuque_id_str)
                    if old_path and old_path != rel_path:
                        # 删除旧文件
                        old_file = self.output_dir / old_path
                        if old_file.exists():
                            try:
                                old_file.unlink()
                                logger.info(f"[Sync] 文档移动，删除旧路径: {old_path}")
                            except OSError:
                                pass

                # 写入新文件
                content = self._build_markdown(detail, author)
                out_file.write_text(content, encoding="utf-8")

                # 更新索引
                if yuque_id:
                    repo_index[str(yuque_id)] = rel_path

                # 收集元数据（用于构建搜索索引）
                book = detail.get("book", {})
                body = detail.get("body", "") or detail.get("content", "") or ""
                self.doc_metadata.append({
                    "yuque_id": yuque_id,
                    "title": detail.get("title", title),
                    "slug": detail.get("slug", slug),
                    "author": author,
                    "book_name": book.get("name", "") if book else "",
                    "book_namespace": namespace,
                    "created_at": YuqueClient.normalize_timestamp(detail.get("created_at")),
                    "updated_at": YuqueClient.normalize_timestamp(detail.get("updated_at")),
                    "word_count": len(body),
                    "file_path": rel_path,
                })

                stats["docs"] += 1
                logger.debug(f"[Sync] 写入文档: {title}")

            except Exception as e:
                logger.error(f"[Sync] 文档同步失败 {title}: {e}")
                stats["errors"] += 1

        elif doc_type == "TITLE":
            # 分组：创建目录，递归处理子节点
            seg = YuqueClient.slug_safe(title)
            next_parent = f"{parent_path}/{seg}" if parent_path else seg
            (repo_dir / next_parent).mkdir(parents=True, exist_ok=True)
            stats["titles"] += 1

            # 递归处理子节点
            children = toc_list_children(uuid, toc_by_uuid)
            for child in children:
                await self._process_toc_item(
                    namespace, repo_name, repo_dir, child, next_parent, toc_by_uuid, repo_index, stats
                )

    def _resolve_author(self, detail: Dict) -> str:
        """解析作者名

        优先通过 user_id 从团队成员中查找真实姓名。
        """
        # 1. 优先通过 user_id 精确匹配团队成员
        for key in ("last_editor", "creator", "user"):
            obj = detail.get(key)
            if isinstance(obj, dict):
                user_id = obj.get("id")
                if user_id and str(user_id) in self.members:
                    return self.members[str(user_id)].get("name", "")

        # 2. 回退：使用语雀返回的名字
        return YuqueClient.author_name_from_detail(detail)

    def _resolve_basename(self, repo_name: str, parent_path: str, base: str) -> str:
        """解决文件名冲突"""
        key = (repo_name, parent_path)
        if key not in self.used_basenames:
            self.used_basenames[key] = set()

        used = self.used_basenames[key]
        if base not in used:
            used.add(base)
            return base

        # 冲突时添加数字后缀
        i = 2
        while f"{base}_{i}" in used:
            i += 1
        new_base = f"{base}_{i}"
        used.add(new_base)
        return new_base

    def _build_markdown(self, detail: Dict, author: str = "") -> str:
        """构建 Markdown 文件"""
        book = detail.get("book", {})

        # YAML frontmatter
        fm = {
            "id": detail.get("id"),
            "title": detail.get("title", ""),
            "slug": detail.get("slug", ""),
            "created_at": YuqueClient.normalize_timestamp(detail.get("created_at")),
            "updated_at": YuqueClient.normalize_timestamp(detail.get("updated_at")),
        }
        if author:
            fm["author"] = author
        if book.get("name"):
            fm["book_name"] = book["name"]
        if detail.get("description"):
            fm["description"] = detail["description"]

        yaml_block = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()

        # 正文
        body = detail.get("body", "") or detail.get("content", "") or ""

        # 元信息表格
        meta_table = f"| 作者 | 创建时间 | 更新时间 |\n|------|----------|----------|\n| {author or '未知'} | {fm['created_at']} | {fm['updated_at']} |\n\n"

        return f"---\n{yaml_block}\n---\n\n{meta_table}{body}"


async def sync_all_repos(
    client: YuqueClient,
    output_dir: Path,
    members: Optional[Dict[str, Dict]] = None,
    progress_callback: Optional[callable] = None,
) -> Dict:
    """同步所有知识库

    Args:
        client: 语雀客户端
        output_dir: 输出目录
        members: 成员映射
        progress_callback: 进度回调函数 (current, total, repo_name)

    Returns:
        同步统计信息
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 获取用户信息
    user = await client.get_user()
    if not user:
        logger.error("[Sync] 获取用户信息失败，请检查 Token 配置")
        return {"repos_count": 0, "docs": 0, "titles": 0, "errors": 1}

    is_group = user.get("type") == "Group"
    user_id = user.get("id")
    if not user_id:
        logger.error("[Sync] 无法获取用户 ID")
        return {"repos_count": 0, "docs": 0, "titles": 0, "errors": 1}

    # 获取知识库列表
    if is_group:
        repos = await client.get_group_repos(user_id)
    else:
        repos = await client.get_user_repos(user_id)

    logger.info(f"[Sync] 发现 {len(repos)} 个知识库")

    # 读取全局索引（用于检测文档移动）
    global_index = _read_global_index(output_dir)

    # 同步
    syncer = DocSyncer(client, output_dir, members, global_index)
    repos_info = []
    total_stats = {"docs": 0, "titles": 0, "errors": 0, "removed": 0}

    for i, repo in enumerate(repos):
        namespace = repo.get("namespace", "")
        name = repo.get("name", "") or namespace  # 如果 name 为空，使用 namespace
        if not namespace:
            continue

        # 进度回调
        if progress_callback:
            progress_callback(i + 1, len(repos), name)

        logger.info(f"[Sync] [{i+1}/{len(repos)}] {name}")
        stats = await syncer.sync_repo(namespace, name)
        total_stats["docs"] += stats["docs"]
        total_stats["titles"] += stats["titles"]
        total_stats["errors"] += stats["errors"]
        total_stats["removed"] += stats.get("removed", 0)

        repos_info.append({
            "id": repo.get("id"),
            "namespace": namespace,
            "name": name,
            "slug": repo.get("slug", ""),
            "description": repo.get("description", ""),
            "items_count": repo.get("items_count", 0),
        })

    # 保存全局索引
    _write_global_index(output_dir, syncer.global_index)

    # 构建元数据索引（SQLite）
    if syncer.doc_metadata:
        from .doc_index import DocIndex
        db_path = output_dir.parent / "doc_index.db"
        doc_index = DocIndex(str(db_path))
        doc_index.clear()
        doc_index.add_docs(syncer.doc_metadata)
        logger.info(f"[Sync] 元数据索引完成: {len(syncer.doc_metadata)} 篇文档")

    # 保存知识库列表（同时保存两份：一份在 docs 目录，一份在 data 根目录供工具读取）
    repos_file = output_dir / ".repos.json"
    repos_file.write_text(json.dumps(repos_info, ensure_ascii=False, indent=2), encoding="utf-8")

    # 额外保存一份到 data 根目录，供 LLM 工具读取
    repos_cache = output_dir.parent / "yuque_repos.json"
    repos_cache.write_text(json.dumps(repos_info, ensure_ascii=False, indent=2), encoding="utf-8")

    # 清理孤儿知识库目录（不在当前 API 列表中的目录）
    current_dirs = {YuqueClient.slug_safe(r.get("name", "")) for r in repos_info if r.get("name")}

    orphan_dirs = []
    for d in output_dir.iterdir():
        if d.name.startswith(".") or not d.is_dir():
            continue
        if d.name not in current_dirs:
            orphan_dirs.append(d.name)
            logger.info(f"[Sync] 发现孤儿知识库目录: {d.name}")

    if orphan_dirs:
        logger.warning(f"[Sync] 共 {len(orphan_dirs)} 个孤儿目录，使用 /sync clean 清理")

    logger.info(f"[Sync] 完成: {total_stats['docs']} docs, {total_stats['titles']} titles, {total_stats['removed']} removed")
    return {
        "repos_count": len(repos),
        "token_type": "团队" if is_group else "个人",
        **total_stats
    }


def _read_global_index(output_dir: Path) -> Dict[str, str]:
    """读取全局 ID->路径 索引"""
    index_file = output_dir / ".yuque-id-to-path.json"
    if not index_file.exists():
        return {}
    try:
        return json.loads(index_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_global_index(output_dir: Path, index: Dict[str, str]) -> None:
    """写入全局 ID->路径 索引"""
    index_file = output_dir / ".yuque-id-to-path.json"
    index_file.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")