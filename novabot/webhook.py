"""
NovaBot Webhook 处理器
处理语雀 Webhook 事件，同步更新本地文档和索引
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from astrbot.api import logger

from .git_ops import GitOps

if TYPE_CHECKING:
    from ..main import NovaBotPlugin


class WebhookHandler:
    """语雀 Webhook 处理器"""

    def __init__(self, plugin: "NovaBotPlugin"):
        self.plugin = plugin
        self.docs_dir = plugin.storage.data_dir / "yuque_docs"

    async def handle(self, payload: dict) -> dict:
        """处理 Webhook 事件

        Args:
            payload: 语雀 Webhook payload

        Returns:
            处理结果
        """
        # 详细日志：记录原始 payload
        logger.info(f"[Webhook] ========== 开始处理 ==========")

        data = payload.get("data", {})
        action = data.get("action_type", "")
        doc_id = data.get("id")
        book = data.get("book", {})

        # 基本信息
        logger.info(f"[Webhook] 事件类型: {action}")
        logger.info(f"[Webhook] 文档ID: {doc_id}")
        logger.info(f"[Webhook] 文档标题: {data.get('title', '(无标题)')}")
        logger.info(f"[Webhook] 文档slug: {data.get('slug', '(无)')}")
        logger.info(f"[Webhook] 知识库: {book.get('name', '(无)')} (id={book.get('id')})")

        # Debug: 完整 payload（仅在需要时启用）
        logger.debug(f"[Webhook] 完整payload: {json.dumps(payload, ensure_ascii=False)[:500]}")

        if action in ("publish", "update"):
            result = await self._handle_doc_change(payload)
        elif action == "delete":
            result = await self._handle_doc_delete(payload)
        else:
            logger.info(f"[Webhook] 忽略事件类型: {action}")
            result = {"status": "ignored", "action": action}

        logger.info(f"[Webhook] 处理结果: {result}")
        logger.info(f"[Webhook] ========== 处理完成 ==========")
        return result

    async def _handle_doc_change(self, payload: dict) -> dict:
        """处理文档发布/更新事件"""
        data = payload.get("data", {})
        doc_id = data.get("id")
        book = data.get("book", {})

        logger.info(f"[Webhook] → 处理文档变更事件")

        if not book:
            logger.error("[Webhook] 文档事件缺少 book 信息")
            return {"status": "error", "message": "missing book"}

        repo_id = book.get("id")
        repo_name = book.get("name", "") or book.get("slug", "")
        repo_slug = book.get("slug", "")
        slug = data.get("slug", "")

        logger.info(f"[Webhook] 知识库: {repo_name} (id={repo_id}, slug={repo_slug})")

        # 获取语雀客户端
        client = self.plugin._get_client()

        # 获取 TOC（用于解析 slug 和更新 .toc.json）
        toc_list = None
        try:
            toc_list = await client.get_repo_toc(repo_id)
            logger.info(f"[Webhook] 获取 TOC 成功: {len(toc_list)} 个节点")
        except Exception as e:
            logger.warning(f"[Webhook] 获取 TOC 失败: {e}")

        # 解析 slug
        if not slug and toc_list:
            for item in toc_list:
                if item.get("id") == doc_id:
                    slug = item.get("url") or item.get("slug") or item.get("uuid", "")
                    break

        if not slug:
            logger.warning(f"[Webhook] 无法解析 slug: doc_id={doc_id}")
            return {"status": "error", "message": "cannot resolve slug"}

        logger.info(f"[Webhook] 文档 slug: {slug}")

        # 获取文档详情
        logger.info(f"[Webhook] 获取文档详情: repo_id={repo_id}, slug={slug}")

        try:
            detail = await client.get_doc_detail(repo_id, slug)
            logger.info(f"[Webhook] 获取文档详情成功，标题: {detail.get('title', '(无)')}")
        except Exception as e:
            logger.error(f"[Webhook] 获取文档详情失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

        if not detail:
            logger.warning(f"[Webhook] 文档详情为空: repo_id={repo_id}, slug={slug}")
            return {"status": "error", "message": "empty detail"}

        # 获取 namespace（用于目录名，可选）
        namespace = await self._get_namespace(client, repo_id, repo_slug)
        logger.info(f"[Webhook] 知识库 namespace: {namespace or '(无)'}")

        # 写入 Markdown
        logger.info(f"[Webhook] 步骤 1/5: 写入 Markdown 文件")
        repo_dir, rel_path = self._write_markdown(detail, repo_name, namespace)

        # 更新 SQLite
        logger.info(f"[Webhook] 步骤 2/5: 更新 SQLite 索引")
        self._update_doc_index(detail, rel_path)

        # 更新 ChromaDB
        logger.info(f"[Webhook] 步骤 3/5: 更新 ChromaDB 向量")
        self._update_rag(detail, rel_path)

        # 更新 .toc.json（参考 yuque2git）
        logger.info(f"[Webhook] 步骤 4/5: 更新 .toc.json")
        if toc_list:
            self._update_toc_json(repo_dir, toc_list)

        # Git commit
        logger.info(f"[Webhook] 步骤 5/5: Git 提交")
        commit_hash = self._git_commit(rel_path, data.get("action_type", "update"), detail.get("title", ""))
        if commit_hash:
            logger.info(f"[Webhook] Git 提交成功: {commit_hash}")
        else:
            logger.info(f"[Webhook] Git 提交跳过（未启用或失败）")

        return {
            "status": "ok",
            "doc_id": doc_id,
            "title": detail.get("title", ""),
            "path": rel_path,
            "commit": commit_hash,
        }

    async def _handle_doc_delete(self, payload: dict) -> dict:
        """处理文档删除事件"""
        data = payload.get("data", {})
        doc_id = data.get("id")

        logger.info(f"[Webhook] → 处理文档删除事件")

        if not doc_id:
            logger.error("[Webhook] 删除事件缺少 doc_id")
            return {"status": "error", "message": "missing doc_id"}

        logger.info(f"[Webhook] 查找文档: doc_id={doc_id}")

        # 从索引查找文件路径
        from .doc_index import DocIndex

        db_path = self.plugin.storage.data_dir / "doc_index.db"
        doc_index = DocIndex(str(db_path))
        doc_record = doc_index.get_doc_by_yuque_id(doc_id)

        deleted_files = []
        repo_dir = None

        if doc_record:
            file_path = doc_record.get("file_path", "")
            logger.info(f"[Webhook] 找到文档记录: {file_path}")
            if file_path:
                full_path = self.docs_dir / file_path
                repo_dir = full_path.parent  # 知识库目录
                if full_path.exists():
                    try:
                        full_path.unlink()
                        deleted_files.append(file_path)
                        logger.info(f"[Webhook] 删除文件成功: {file_path}")
                    except Exception as e:
                        logger.warning(f"[Webhook] 删除文件失败: {e}")
                else:
                    logger.warning(f"[Webhook] 文件不存在: {file_path}")
        else:
            logger.warning(f"[Webhook] 索引中未找到文档: doc_id={doc_id}")

        # 删除 SQLite 记录
        logger.info(f"[Webhook] 步骤 1/4: 删除 SQLite 记录")
        doc_index.delete_doc(doc_id)
        logger.info(f"[Webhook] SQLite 记录已删除")

        # 删除 ChromaDB 向量
        logger.info(f"[Webhook] 步骤 2/4: 删除 ChromaDB 向量")
        if self.plugin.rag:
            self.plugin.rag.delete_doc(doc_id)
            logger.info(f"[Webhook] ChromaDB 向量已删除")
        else:
            logger.info(f"[Webhook] RAG 未初始化，跳过")

        # 更新 .toc.json
        logger.info(f"[Webhook] 步骤 3/4: 更新 .toc.json")
        if repo_dir:
            self._remove_from_toc_json(repo_dir, doc_id)

        # Git commit
        logger.info(f"[Webhook] 步骤 4/4: Git 提交")
        if deleted_files:
            commit_hash = self._git_commit(deleted_files, "delete", f"doc_id={doc_id}")
            if commit_hash:
                logger.info(f"[Webhook] Git 提交成功: {commit_hash}")

        return {
            "status": "ok",
            "doc_id": doc_id,
            "deleted_files": deleted_files,
        }

    async def _get_namespace(self, client, repo_id: int, repo_slug: str) -> Optional[str]:
        """获取知识库 namespace"""
        # 尝试从缓存获取
        repos_file = self.docs_dir / ".repos.json"
        if repos_file.exists():
            try:
                repos = json.loads(repos_file.read_text(encoding="utf-8"))
                for repo in repos:
                    if repo.get("id") == repo_id or repo.get("slug") == repo_slug:
                        return repo.get("namespace", "")
            except Exception:
                pass

        # 从 API 获取（使用 repo_id）
        try:
            repo_detail = await client.get_repo(repo_id)
            return repo_detail.get("namespace", "")
        except Exception as e:
            logger.warning(f"[Webhook] 获取知识库详情失败: {e}")

        return None

    def _write_markdown(self, detail: dict, repo_name: str, namespace: Optional[str]) -> tuple:
        """写入 Markdown 文件

        Returns:
            (repo_dir, rel_path)
        """
        from .yuque_client import YuqueClient

        self.docs_dir.mkdir(parents=True, exist_ok=True)

        # 知识库目录名：优先用 repo_name，备选用 namespace
        if repo_name:
            dir_name = YuqueClient.slug_safe(repo_name)
        elif namespace:
            dir_name = namespace.replace("/", "_")
        else:
            dir_name = "unknown"
        repo_dir = self.docs_dir / dir_name
        repo_dir.mkdir(parents=True, exist_ok=True)

        # 文档标题和 slug
        title = detail.get("title", "无标题")
        slug = detail.get("slug", "")
        doc_id = detail.get("id", 0)

        # 文件名
        base = YuqueClient.doc_basename(title, slug) or "untitled"
        out_file = repo_dir / f"{base}.md"

        # 检查是否有同 ID 的旧文件（文档移动/重命名）
        old_file = self._find_doc_file(doc_id)
        if old_file and old_file != out_file:
            # 删除旧文件
            try:
                old_file.unlink()
                logger.info(f"[Webhook] 删除旧文件: {old_file.relative_to(self.docs_dir)}")
            except Exception:
                pass

        # 构建作者名
        author = self._resolve_author(detail)

        # 构建 YAML frontmatter
        book = detail.get("book", {})
        fm = {
            "id": doc_id,
            "title": title,
            "slug": slug,
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

        content = f"---\n{yaml_block}\n---\n\n{meta_table}{body}"
        out_file.write_text(content, encoding="utf-8")

        rel_path = str(out_file.relative_to(self.docs_dir))
        logger.info(f"[Webhook] 写入文件: {rel_path}")

        return repo_dir, rel_path

    def _resolve_author(self, detail: dict) -> str:
        """解析文档作者名"""
        # 从 detail 的 creator/user/last_editor 获取
        for key in ("creator", "user", "last_editor"):
            obj = detail.get(key)
            if isinstance(obj, dict):
                name = obj.get("name", "") or obj.get("login", "")
                if name:
                    return name

        return ""

    def _find_doc_file(self, doc_id: int) -> Optional[Path]:
        """根据文档 ID 查找文件"""
        if not doc_id:
            return None

        for md_file in self.docs_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                if content.startswith("---"):
                    end = content.find("\n---", 3)
                    if end != -1:
                        fm = yaml.safe_load(content[3:end].strip())
                        if fm and fm.get("id") == doc_id:
                            return md_file
            except Exception:
                continue

        return None

    def _update_doc_index(self, detail: dict, rel_path: str):
        """更新 SQLite 元数据索引"""
        from .doc_index import DocIndex

        db_path = self.plugin.storage.data_dir / "doc_index.db"

        try:
            doc_index = DocIndex(str(db_path))

            book = detail.get("book", {})
            body = detail.get("body", "") or detail.get("content", "") or ""
            author = self._resolve_author(detail)

            doc_index.add_doc({
                "yuque_id": detail.get("id"),
                "title": detail.get("title", ""),
                "slug": detail.get("slug", ""),
                "author": author,
                "book_name": book.get("name", "") if book else "",
                "book_namespace": book.get("namespace", "") if book else "",
                "created_at": detail.get("created_at", ""),
                "updated_at": detail.get("updated_at", ""),
                "word_count": len(body),
                "file_path": rel_path,
            })

            logger.info(f"[Webhook] 更新索引: {detail.get('title', '')}")
        except Exception as e:
            logger.error(f"[Webhook] 更新索引失败: {e}")

    def _update_rag(self, detail: dict, rel_path: str):
        """更新 ChromaDB 向量索引"""
        if not self.plugin.rag:
            return

        try:
            # 读取文件内容（去掉 frontmatter）
            file_path = self.docs_dir / rel_path
            content = file_path.read_text(encoding="utf-8")

            body = content
            if content.startswith("---"):
                end = content.find("\n---", 3)
                if end != -1:
                    body = content[end + 4:].strip()

            # 去掉元信息表格
            lines = body.split('\n')
            content_start = 0
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not stripped:
                    content_start = i + 1
                    continue
                if stripped.startswith('|') or re.match(r'^\|[-:\s|]+\|$', stripped):
                    content_start = i + 1
                    continue
                break
            body = '\n'.join(lines[content_start:]).strip()

            book = detail.get("book", {})

            self.plugin.rag.upsert_doc({
                "id": detail.get("id"),
                "content": body,
                "title": detail.get("title", ""),
                "slug": detail.get("slug", ""),
                "author": self._resolve_author(detail),
                "book_name": book.get("name", "") if book else "",
                "repo_namespace": str(file_path.parent.relative_to(self.docs_dir)),
            })

            logger.info(f"[Webhook] 更新向量: {detail.get('title', '')}")
        except Exception as e:
            logger.error(f"[Webhook] 更新向量失败: {e}")

    def _git_commit(self, files, action: str, title: str) -> Optional[str]:
        """Git 提交"""
        if not self.plugin.config.get("git_enabled", True):
            return None

        git = GitOps(self.docs_dir)
        if not git.ensure_git():
            return None

        if isinstance(files, str):
            files = [files]

        message = f"yuque: {action} {title}"
        return git.add_commit(files, message)

    def _update_toc_json(self, repo_dir: Path, toc_list: list):
        """更新知识库的 .toc.json 文件"""
        toc_file = repo_dir / ".toc.json"
        try:
            toc_file.write_text(
                json.dumps(toc_list, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            logger.info(f"[Webhook] 更新 .toc.json: {toc_file.relative_to(self.docs_dir)}")
        except Exception as e:
            logger.warning(f"[Webhook] 更新 .toc.json 失败: {e}")

    def _remove_from_toc_json(self, repo_dir: Path, doc_id: int):
        """从 .toc.json 中移除已删除的文档"""
        toc_file = repo_dir / ".toc.json"
        if not toc_file.exists():
            return

        try:
            toc_list = json.loads(toc_file.read_text(encoding="utf-8"))
            # 过滤掉已删除的文档
            new_toc = [item for item in toc_list if item.get("id") != doc_id]

            if len(new_toc) != len(toc_list):
                toc_file.write_text(
                    json.dumps(new_toc, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                logger.info(f"[Webhook] 从 .toc.json 移除文档: doc_id={doc_id}")
        except Exception as e:
            logger.warning(f"[Webhook] 更新 .toc.json 失败: {e}")