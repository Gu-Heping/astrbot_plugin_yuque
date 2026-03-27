"""
NovaBot Webhook 处理器
处理语雀 Webhook 事件，同步更新本地文档和索引
支持智能推送订阅
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import yaml

from astrbot.api import logger

from .git_ops import GitOps
from .rag import RAGEngine
from .sync import toc_list_children
from .yuque_client import YuqueClient

if TYPE_CHECKING:
    from .push_notifier import PushNotifier
    from .storage import Storage
    from .subscribe import SubscriptionManager


class WebhookHandler:
    """语雀 Webhook 处理器"""

    def __init__(
        self,
        docs_dir: Path,
        data_dir: Path,
        get_client: Callable[[], YuqueClient],
        rag: Optional[RAGEngine],
        config: dict,
        push_notifier: Optional["PushNotifier"] = None,
        subscription_manager: Optional["SubscriptionManager"] = None,
        storage: Optional["Storage"] = None,
    ):
        """
        初始化 Webhook 处理器

        Args:
            docs_dir: 文档目录
            data_dir: 数据目录
            get_client: 获取语雀客户端的回调函数
            rag: RAG 引擎实例
            config: 配置字典
            push_notifier: 推送管理器（可选）
            subscription_manager: 订阅管理器（可选）
            storage: 存储实例（可选，用于匹配团队成员）
        """
        self.docs_dir = docs_dir
        self.data_dir = data_dir
        self.get_client = get_client
        self.rag = rag
        self.config = config
        self.push_notifier = push_notifier
        self.subscription_manager = subscription_manager
        self.storage = storage

    def _match_editor_name(self, detail: dict) -> Optional[str]:
        """匹配文档编辑者姓名（用于推送消息）

        优先通过 last_editor_id 匹配，回退到名字模糊匹配。

        Args:
            detail: 文档详情

        Returns:
            匹配到的团队成员真实姓名，未匹配返回 None
        """
        if not self.storage:
            return None

        try:
            # 1. 优先通过 last_editor_id 匹配（文档详情 API 不返回 last_editor 对象）
            last_editor_id = detail.get("last_editor_id")
            if last_editor_id:
                member = self.storage.find_member_by_id(str(last_editor_id))
                if member:
                    return member.get("name")

            # 2. 尝试从嵌套对象获取（文档列表 API 会返回 last_editor 对象）
            for key in ("last_editor", "creator", "user"):
                obj = detail.get(key)
                if isinstance(obj, dict):
                    user_id = obj.get("id")
                    if user_id:
                        member = self.storage.find_member_by_id(str(user_id))
                        if member:
                            return member.get("name")

            # 3. 回退：通过语雀用户名模糊匹配
            yuque_name = YuqueClient.author_name_from_detail(detail)
            if yuque_name:
                member = self.storage.find_member_by_name(yuque_name)
                if member:
                    return member.get("name", yuque_name)

        except Exception as e:
            logger.debug(f"[Webhook] 匹配编辑者失败: {e}")

        return None

    def _match_creator_name(self, detail: dict) -> Optional[str]:
        """匹配文档创建者姓名（用于文档元数据）

        Args:
            detail: 文档详情

        Returns:
            匹配到的团队成员真实姓名，未匹配返回 None
        """
        if not self.storage:
            return None

        try:
            # 1. 优先通过 user_id/creator_id 匹配
            creator_id = detail.get("user_id") or detail.get("creator_id")
            if creator_id:
                member = self.storage.find_member_by_id(str(creator_id))
                if member:
                    return member.get("name")

            # 2. 尝试从嵌套对象获取
            for key in ("creator", "user"):
                obj = detail.get(key)
                if isinstance(obj, dict):
                    user_id = obj.get("id")
                    if user_id:
                        member = self.storage.find_member_by_id(str(user_id))
                        if member:
                            return member.get("name")

        except Exception as e:
            logger.debug(f"[Webhook] 匹配创建者失败: {e}")

        return None

    def _resolve_author(self, detail: dict) -> str:
        """解析文档作者名"""
        return YuqueClient.author_name_from_detail(detail)

    def _find_toc_item_path(self, toc_list: list, doc_id: int) -> Optional[str]:
        """根据 TOC 解析文档所在的相对子目录"""
        if not toc_list or not doc_id:
            return None

        toc_by_uuid = {item["uuid"]: item for item in toc_list if item.get("uuid")}

        def walk(parent_uuid: Optional[str], parent_path: str) -> Optional[str]:
            for item in toc_list_children(parent_uuid, toc_by_uuid):
                item_type = item.get("type", "DOC")
                title = item.get("title", "无标题")
                if item_type == "TITLE":
                    segment = YuqueClient.slug_safe(title)
                    next_path = f"{parent_path}/{segment}" if parent_path else segment
                    result = walk(item.get("uuid"), next_path)
                    if result is not None:
                        return result
                    continue

                if item.get("id") == doc_id:
                    return parent_path

            return None

        return walk(None, "")

    def _resolve_doc_output(self, detail: dict, repo_name: str, namespace: Optional[str], toc_list: Optional[list]) -> tuple[Path, Path, str]:
        """统一解析文档输出目录与相对路径"""
        self.docs_dir.mkdir(parents=True, exist_ok=True)

        if repo_name:
            dir_name = YuqueClient.slug_safe(repo_name)
        elif namespace:
            dir_name = namespace.replace("/", "_")
        else:
            dir_name = "unknown"

        repo_dir = self.docs_dir / dir_name
        repo_dir.mkdir(parents=True, exist_ok=True)

        title = detail.get("title", "无标题")
        slug = detail.get("slug", "")
        doc_id = detail.get("id", 0)
        base = YuqueClient.doc_basename(title, slug) or "untitled"

        relative_parent = self._find_toc_item_path(toc_list or [], doc_id) or ""
        target_dir = repo_dir / relative_parent if relative_parent else repo_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        out_file = target_dir / f"{base}.md"
        rel_path = str(out_file.relative_to(self.docs_dir))
        return repo_dir, out_file, rel_path

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

        client = self.get_client()

        # 获取 TOC
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
        try:
            detail = await client.get_doc_detail(repo_id, slug)
            logger.info(f"[Webhook] 获取文档详情成功，标题: {detail.get('title', '(无)')}")
        except Exception as e:
            logger.error(f"[Webhook] 获取文档详情失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

        if not detail:
            logger.warning(f"[Webhook] 文档详情为空: repo_id={repo_id}, slug={slug}")
            return {"status": "error", "message": "empty detail"}

        # 获取 namespace
        namespace = await self._get_namespace(client, repo_id, repo_slug)
        logger.info(f"[Webhook] 知识库 namespace: {namespace or '(无)'}")

        # 统一路径解析（与全量同步保持一致）
        logger.info(f"[Webhook] 步骤 1/5: 解析文档路径")
        repo_dir, out_file, rel_path = self._resolve_doc_output(detail, repo_name, namespace, toc_list)

        # 处理文档移动（删除旧路径文件）
        old_record = self._get_old_record(doc_id)
        if old_record:
            old_path = old_record.get("file_path")
            if old_path and old_path != rel_path:
                old_file = self.docs_dir / old_path
                if old_file.exists():
                    try:
                        old_file.unlink()
                        logger.info(f"[Webhook] 删除旧路径文件: {old_path}")
                    except Exception as e:
                        logger.warning(f"[Webhook] 删除旧文件失败: {e}")

        # 写入 Markdown
        logger.info(f"[Webhook] 步骤 2/5: 写入 Markdown 文件")
        self._write_markdown_file(out_file, detail, repo_dir)

        # 更新 SQLite
        logger.info(f"[Webhook] 步骤 3/5: 更新 SQLite 索引")
        self._update_doc_index(detail, rel_path)

        # 更新 ChromaDB
        logger.info(f"[Webhook] 步骤 4/5: 更新 ChromaDB 向量")
        self._update_rag(detail, rel_path)

        # 更新 .toc.json
        logger.info(f"[Webhook] 步骤 5/5: 更新 .toc.json")
        if toc_list:
            self._update_toc_json(repo_dir, toc_list)

        # Git commit
        commit_hash = self._git_commit(rel_path, data.get("action_type", "update"), detail.get("title", ""))
        if commit_hash:
            logger.info(f"[Webhook] Git 提交成功: {commit_hash}")

        # 智能推送判断
        push_result = await self._handle_push(doc_id, commit_hash, rel_path, detail)

        return {
            "status": "ok",
            "doc_id": doc_id,
            "title": detail.get("title", ""),
            "path": rel_path,
            "commit": commit_hash,
            "push": push_result,
        }

    async def _handle_push(
        self,
        doc_id: int,
        commit_hash: Optional[str],
        rel_path: str,
        detail: dict
    ) -> Optional[dict]:
        """处理智能推送

        Args:
            doc_id: 文档 ID
            commit_hash: Git commit hash
            rel_path: 文档相对路径
            detail: 文档详情

        Returns:
            推送结果
        """
        if not self.push_notifier:
            return None

        if not commit_hash:
            logger.debug("[Push] 无 commit hash，跳过推送")
            return None

        if not self.push_notifier.should_enable():
            logger.debug("[Push] 推送功能已禁用")
            return None

        try:
            # 1. 获取 diff
            diff, is_first_push = self.push_notifier.get_diff(doc_id, commit_hash, rel_path)
            logger.info(f"[Push] diff 长度: {len(diff)} 字符, 首次推送: {is_first_push}")

            # 2. 预处理检查（首次推送跳过预处理）
            should_skip, reason = self.push_notifier.pre_check(diff, is_first_push)
            if should_skip:
                logger.info(f"[Push] 跳过推送: {reason}")
                return {"skipped": True, "reason": reason}

            # 3. 构建文档信息
            book = detail.get("book", {})
            namespace = detail.get("namespace", "") or book.get("namespace", "")
            slug = detail.get("slug", "")

            # 构建文档 URL
            doc_url = ""
            if namespace and slug:
                doc_url = f"https://www.yuque.com/{namespace}/{slug}"

            # 推送消息显示编辑者（实际更新文档的人）
            # 优先使用 actor（Webhook 操作者），回退到 last_editor_id
            editor_name = self._match_editor_name(detail) or self._resolve_author(detail)

            doc_info = {
                "id": doc_id,
                "title": detail.get("title", ""),
                "author": editor_name,  # 推送消息显示编辑者
                "book_name": book.get("name", "") if book else "",
                "path": rel_path,
                "url": doc_url,
            }

            # 4. 首次推送时读取文档内容
            doc_content = diff  # 默认使用 diff
            if is_first_push:
                doc_file = self.docs_dir / rel_path
                if doc_file.exists():
                    try:
                        content = doc_file.read_text(encoding="utf-8")
                        # 去掉 frontmatter
                        _, body = YuqueClient.parse_frontmatter(content)
                        # 截取前 2000 字符
                        doc_content = body[:2000]
                        if len(body) > 2000:
                            doc_content += "\n... (内容已截断)"
                        logger.info(f"[Push] 首次推送，使用文档原文 ({len(body)} 字符)")
                    except Exception as e:
                        logger.warning(f"[Push] 读取文档失败: {e}")

            # 5. LLM 判断是否推送
            should_push, summary = await self.push_notifier.agent_should_push(doc_info, doc_content, is_first_push)

            if should_push:
                # 6. 推送给订阅者
                await self.push_notifier.notify_subscribers(doc_info, summary)
                # 7. 记录推送
                self.push_notifier.mark_pushed(doc_id, commit_hash)
                return {"pushed": True, "summary": summary}
            else:
                logger.info(f"[Push] LLM 判断不推送: {summary.get('reason', '')}")
                return {"pushed": False, "reason": summary.get("reason", "")}

        except Exception as e:
            logger.error(f"[Push] 推送处理失败: {e}", exc_info=True)
            return {"error": str(e)}

    async def _handle_doc_delete(self, payload: dict) -> dict:
        """处理文档删除事件"""
        data = payload.get("data", {})
        doc_id = data.get("id")
        book = data.get("book", {})

        logger.info(f"[Webhook] → 处理文档删除事件")

        if not doc_id:
            logger.error("[Webhook] 删除事件缺少 doc_id")
            return {"status": "error", "message": "missing doc_id"}

        old_record = self._get_old_record(doc_id)
        deleted_files = []
        repo_dir = None

        if old_record:
            file_path = old_record.get("file_path", "")
            logger.info(f"[Webhook] 找到文档记录: {file_path}")
            if file_path:
                full_path = self.docs_dir / file_path
                if full_path.exists():
                    try:
                        full_path.unlink()
                        deleted_files.append(file_path)
                        logger.info(f"[Webhook] 删除文件成功: {file_path}")
                    except Exception as e:
                        logger.warning(f"[Webhook] 删除文件失败: {e}")

                try:
                    repo_dir = self.docs_dir / Path(file_path).parts[0]
                except Exception:
                    repo_dir = full_path.parent
        else:
            logger.warning(f"[Webhook] 索引中未找到文档: doc_id={doc_id}")

        # 删除 SQLite 记录
        logger.info(f"[Webhook] 步骤 1/4: 删除 SQLite 记录")
        from .doc_index import DocIndex

        db_path = self.data_dir / "doc_index.db"
        with DocIndex(str(db_path)) as doc_index:
            doc_index.delete_doc(doc_id)

        # 删除 ChromaDB 向量
        logger.info(f"[Webhook] 步骤 2/4: 删除 ChromaDB 向量")
        if self.rag:
            self.rag.delete_doc(doc_id)

        # 更新 .toc.json：优先重新拉取完整 TOC 覆盖
        logger.info(f"[Webhook] 步骤 3/4: 更新 .toc.json")
        if repo_dir and book.get("id"):
            try:
                client = self.get_client()
                toc_list = await client.get_repo_toc(book.get("id"))
                self._update_toc_json(repo_dir, toc_list)
            except Exception as e:
                logger.warning(f"[Webhook] 重新获取 TOC 失败，回退到本地删除: {e}")
                self._remove_from_toc_json(repo_dir, doc_id)
        elif repo_dir:
            self._remove_from_toc_json(repo_dir, doc_id)

        # Git commit
        logger.info(f"[Webhook] 步骤 4/4: Git 提交")
        commit_hash = None
        if deleted_files:
            commit_hash = self._git_commit(deleted_files, "delete", f"doc_id={doc_id}")
            if commit_hash:
                logger.info(f"[Webhook] Git 提交成功: {commit_hash}")

        return {
            "status": "ok",
            "doc_id": doc_id,
            "deleted_files": deleted_files,
            "commit": commit_hash,
        }

    def _get_old_record(self, doc_id: int) -> Optional[dict]:
        """从 SQLite 索引获取文档旧记录"""
        if not doc_id:
            return None

        from .doc_index import DocIndex

        try:
            db_path = self.data_dir / "doc_index.db"
            with DocIndex(str(db_path)) as doc_index:
                return doc_index.get_doc_by_yuque_id(doc_id)
        except Exception as e:
            logger.debug(f"[Webhook] 读取旧索引失败: {e}")
            return None

    def _write_markdown_file(self, out_file: Path, detail: dict, repo_dir: Path):
        """写入 Markdown 文件"""
        out_file.parent.mkdir(parents=True, exist_ok=True)

        # 优先使用团队成员真实姓名
        author = self._match_creator_name(detail) or self._resolve_author(detail)
        book = detail.get("book", {})
        fm = {
            "id": detail.get("id", 0),
            "title": detail.get("title", "无标题"),
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
        body = detail.get("body", "") or detail.get("content", "") or ""
        meta_table = (
            "| 作者 | 创建时间 | 更新时间 |\n"
            "|------|----------|----------|\n"
            f"| {author or '未知'} | {fm['created_at']} | {fm['updated_at']} |\n\n"
        )

        content = f"---\n{yaml_block}\n---\n\n{meta_table}{body}"
        out_file.write_text(content, encoding="utf-8")
        logger.info(f"[Webhook] 写入文件: {out_file.relative_to(self.docs_dir)}")

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

    def _update_doc_index(self, detail: dict, rel_path: str):
        """更新 SQLite 元数据索引"""
        from .doc_index import DocIndex

        db_path = self.data_dir / "doc_index.db"

        try:
            with DocIndex(str(db_path)) as doc_index:
                book = detail.get("book", {})
                body = detail.get("body", "") or detail.get("content", "") or ""
                # 优先使用团队成员真实姓名（创建者）
                author = self._match_creator_name(detail) or self._resolve_author(detail)

                doc_index.add_doc({
                    "yuque_id": detail.get("id"),
                    "title": detail.get("title", ""),
                    "slug": detail.get("slug", ""),
                    "author": author,
                    "book_name": book.get("name", "") if book else "",
                    "book_namespace": book.get("namespace", "") if book else "",
                    "created_at": YuqueClient.normalize_timestamp(detail.get("created_at")),
                    "updated_at": YuqueClient.normalize_timestamp(detail.get("updated_at")),
                    "word_count": len(body),
                    "file_path": rel_path,
                })

            logger.info(f"[Webhook] 更新索引: {detail.get('title', '')}")
        except Exception as e:
            logger.error(f"[Webhook] 更新索引失败: {e}")

    def _update_rag(self, detail: dict, rel_path: str):
        """更新 ChromaDB 向量索引"""
        if not self.rag:
            return

        try:
            # 读取文件内容（去掉 frontmatter）
            file_path = self.docs_dir / rel_path
            content = file_path.read_text(encoding="utf-8")

            # 解析 frontmatter
            _, body = YuqueClient.parse_frontmatter(content)

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

            self.rag.upsert_doc({
                "id": detail.get("id"),
                "content": body,
                "title": detail.get("title", ""),
                "slug": detail.get("slug", ""),
                "author": self._match_creator_name(detail) or self._resolve_author(detail),
                "book_name": book.get("name", "") if book else "",
                "repo_namespace": str(file_path.parent.relative_to(self.docs_dir)),
            })

            logger.info(f"[Webhook] 更新向量: {detail.get('title', '')}")
        except Exception as e:
            logger.error(f"[Webhook] 更新向量失败: {e}")

    def _git_commit(self, files, action: str, title: str) -> Optional[str]:
        """Git 提交"""
        if not self.config.get("git_enabled", True):
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