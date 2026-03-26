"""
NovaBot 文档同步模块
基于 yuque2git 实现，支持 TOC 层级处理
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    ):
        self.client = client
        self.output_dir = output_dir
        self.members = members or {}
        self.used_basenames: Dict[tuple, set] = {}  # (repo_name, parent_path) -> set of basenames

    async def sync_repo(self, namespace: str, repo_name: str) -> Dict:
        """同步单个知识库

        Args:
            namespace: 知识库命名空间
            repo_name: 知识库名称

        Returns:
            同步统计信息
        """
        repo_dir = self.output_dir / YuqueClient.slug_safe(repo_name)
        repo_dir.mkdir(parents=True, exist_ok=True)

        # 获取 TOC
        toc_list = await self.client.get_repo_toc(namespace)
        toc_by_uuid = {n["uuid"]: n for n in toc_list if n.get("uuid")}

        # 保存 TOC
        toc_file = repo_dir / ".toc.json"
        toc_file.write_text(json.dumps(toc_list, ensure_ascii=False, indent=2), encoding="utf-8")

        # 处理 TOC 节点
        roots = toc_list_children(None, toc_by_uuid)
        stats = {"docs": 0, "titles": 0, "errors": 0}
        index = {}  # yuque_id -> path

        for item in roots:
            await self._process_toc_item(
                namespace, repo_name, repo_dir, item, "", toc_by_uuid, index, stats
            )

        # 保存索引
        if index:
            index_file = repo_dir / ".index.json"
            index_file.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(f"[Sync] {repo_name}: {stats['docs']} docs, {stats['titles']} titles")
        return stats

    async def _process_toc_item(
        self,
        namespace: str,
        repo_name: str,
        repo_dir: Path,
        toc_item: Dict,
        parent_path: str,
        toc_by_uuid: Dict[str, Dict],
        index: Dict[str, str],
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
                content = self._build_markdown(detail, author)
                out_file.write_text(content, encoding="utf-8")

                # 更新索引
                rel_path = str(out_file.relative_to(self.output_dir))
                if yuque_id:
                    index[str(yuque_id)] = rel_path

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
                    namespace, repo_name, repo_dir, child, next_parent, toc_by_uuid, index, stats
                )

    def _resolve_author(self, detail: Dict) -> str:
        """解析作者名"""
        user_id = detail.get("user_id") or detail.get("last_editor_id")
        if user_id and str(user_id) in self.members:
            return self.members[str(user_id)].get("name", "")
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

    # 同步
    syncer = DocSyncer(client, output_dir, members)
    repos_info = []
    total_stats = {"docs": 0, "titles": 0, "errors": 0}

    for i, repo in enumerate(repos):
        namespace = repo.get("namespace", "")
        name = repo.get("name", "")
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

        repos_info.append({
            "id": repo.get("id"),
            "namespace": namespace,
            "name": name,
            "slug": repo.get("slug", ""),
            "description": repo.get("description", ""),
            "items_count": repo.get("items_count", 0),
        })

    # 保存知识库列表
    repos_file = output_dir / ".repos.json"
    repos_file.write_text(json.dumps(repos_info, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"[Sync] 完成: {total_stats['docs']} docs, {total_stats['titles']} titles")
    return {
        "repos_count": len(repos),
        "token_type": "团队" if is_group else "个人",
        **total_stats
    }