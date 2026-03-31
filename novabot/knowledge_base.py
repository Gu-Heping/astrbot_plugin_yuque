"""
NovaBot 知识库层模块
以知识库为单位的服务能力
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Any

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.star import Context

    from .doc_index import DocIndex
    from .rag import RAGEngine
    from .token_monitor import TokenMonitor


class KnowledgeBaseManager:
    """知识库管理器

    功能：
    - 列出知识库
    - 知识库概览
    - 范围检索
    """

    def __init__(self, doc_index: "DocIndex", rag: Optional["RAGEngine"] = None, docs_dir: Optional[Path] = None):
        self.doc_index = doc_index
        self.rag = rag
        self.docs_dir = docs_dir

    def list_kbs(self) -> list[dict]:
        """列出所有知识库

        Returns:
            [{book_name, doc_count, total_words}, ...]
        """
        return self.doc_index.list_books()

    def get_kb_info(self, book_name: str) -> Optional[dict]:
        """获取单个知识库概览

        Args:
            book_name: 知识库名称

        Returns:
            {
                book_name: str,
                doc_count: int,
                total_words: int,
                contributors: [{author, doc_count, total_words}, ...],
                recent_updates: [{title, author, updated_at}, ...],
                latest_update: str,
            }
        """
        # 模糊匹配知识库名
        books = self.doc_index.list_books()
        matched = None
        for b in books:
            if book_name.lower() in b.get("book_name", "").lower():
                matched = b
                break

        if not matched:
            return None

        book_name_actual = matched["book_name"]

        # 获取贡献者
        contributors = self.get_kb_contributors(book_name_actual)

        # 获取最近更新
        recent_updates = self.get_kb_recent_updates(book_name_actual, limit=5)

        # 最新更新时间
        latest_update = ""
        if recent_updates:
            latest_update = recent_updates[0].get("updated_at", "")

        return {
            "book_name": book_name_actual,
            "doc_count": matched.get("doc_count", 0),
            "total_words": matched.get("total_words", 0),
            "contributors": contributors,
            "recent_updates": recent_updates,
            "latest_update": latest_update,
        }

    def get_kb_contributors(self, book_name: str, limit: int = 10) -> list[dict]:
        """获取知识库贡献者

        Args:
            book_name: 知识库名称
            limit: 最大返回数量

        Returns:
            [{author, doc_count, total_words}, ...]
        """
        try:
            from .doc_index import sqlite3
            conn = self.doc_index._get_conn()
            rows = conn.execute("""
                SELECT author, COUNT(*) as doc_count, SUM(word_count) as total_words
                FROM docs
                WHERE book_name = ? AND author != ''
                GROUP BY author
                ORDER BY doc_count DESC
                LIMIT ?
            """, (book_name, limit)).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[KB] 获取贡献者失败: {e}")
            return []

    def get_kb_recent_updates(self, book_name: str, limit: int = 10) -> list[dict]:
        """获取知识库最近更新

        Args:
            book_name: 知识库名称
            limit: 最大返回数量

        Returns:
            [{title, author, updated_at}, ...]
        """
        try:
            conn = self.doc_index._get_conn()
            rows = conn.execute("""
                SELECT title, author, updated_at
                FROM docs
                WHERE book_name = ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (book_name, limit)).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[KB] 获取最近更新失败: {e}")
            return []

    def get_kb_structure(self, book_name: str) -> Optional[dict]:
        """获取知识库目录结构

        Args:
            book_name: 知识库名称

        Returns:
            {
                book_name: str,
                toc_tree: [{type, title, depth, children: [...]}],  # 树状结构
            }
        """
        # 模糊匹配知识库名
        books = self.doc_index.list_books()
        matched = None
        for b in books:
            if book_name.lower() in b.get("book_name", "").lower():
                matched = b
                break

        if not matched:
            return None

        book_name_actual = matched["book_name"]

        try:
            # 尝试从 .toc.json 读取 TOC 结构
            toc_tree = []
            if self.docs_dir:
                from .yuque_client import YuqueClient
                dir_name = YuqueClient.slug_safe(book_name_actual)
                toc_file = self.docs_dir / dir_name / ".toc.json"

                if toc_file.exists():
                    toc_list = json.loads(toc_file.read_text(encoding="utf-8"))
                    toc_by_uuid = {n.get("uuid"): n for n in toc_list if n.get("uuid")}
                    from .sync import toc_list_children

                    # 递归构建树状结构
                    roots = toc_list_children(None, toc_by_uuid)
                    toc_tree = self._build_toc_tree(roots, toc_by_uuid, depth=0)

            return {
                "book_name": book_name_actual,
                "toc_tree": toc_tree,
            }

        except Exception as e:
            logger.error(f"[KB] 获取结构失败: {e}")
            return None

    def _build_toc_tree(self, nodes: list, toc_by_uuid: dict, depth: int) -> list:
        """递归构建 TOC 树状结构

        Args:
            nodes: 当前层级的节点列表
            toc_by_uuid: UUID -> node 映射
            depth: 当前深度

        Returns:
            [{type, title, depth, children}, ...]
        """
        from .sync import toc_list_children

        tree = []
        for node in nodes:
            node_type = node.get("type", "DOC")
            title = node.get("title", "")
            uuid = node.get("uuid")

            # 递归获取子节点
            children = toc_list_children(uuid, toc_by_uuid)
            child_tree = self._build_toc_tree(children, toc_by_uuid, depth + 1) if children else []

            tree.append({
                "type": node_type,
                "title": title,
                "depth": depth,
                "children": child_tree,
            })

        return tree

    def search_in_kb(self, book_name: str, query: str, k: int = 5) -> list[dict]:
        """在指定知识库范围内检索

        Args:
            book_name: 知识库名称
            query: 搜索查询
            k: 返回数量

        Returns:
            搜索结果列表
        """
        if not self.rag:
            return []

        return self.rag.search(query, k=k, book_filter=book_name)

    def get_doc_content(self, book_name: str, title: str) -> Optional[dict]:
        """获取文档完整内容

        Args:
            book_name: 知识库名称
            title: 文档标题（支持模糊匹配）

        Returns:
            {title, author, book_name, content} 或 None
        """
        if not self.docs_dir:
            logger.warning("[KB] docs_dir 未设置，无法读取文档")
            return None

        try:
            conn = self.doc_index._get_conn()

            # 模糊匹配知识库
            books = self.doc_index.list_books()
            matched_book = None
            for b in books:
                if book_name.lower() in b.get("book_name", "").lower():
                    matched_book = b.get("book_name")
                    break

            if not matched_book:
                return None

            # 模糊匹配标题
            rows = conn.execute("""
                SELECT title, author, book_name, file_path
                FROM docs
                WHERE book_name = ? AND title LIKE ?
                ORDER BY word_count DESC
                LIMIT 1
            """, (matched_book, f"%{title}%")).fetchall()

            if not rows:
                return None

            row = dict(rows[0])  # 转换为 dict 以支持 .get()
            file_path = row.get("file_path")

            if not file_path:
                return None

            # 读取文件内容
            doc_file = self.docs_dir / file_path
            if not doc_file.exists():
                logger.warning(f"[KB] 文档文件不存在: {file_path}")
                return None

            content = doc_file.read_text(encoding="utf-8")

            # 解析 frontmatter，提取正文
            # 去掉 YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()

            # 去掉元信息表格
            lines = content.split('\n')
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
            content = '\n'.join(lines[content_start:]).strip()

            return {
                "title": row.get("title"),
                "author": row.get("author"),
                "book_name": row.get("book_name"),
                "content": content,
            }

        except Exception as e:
            logger.error(f"[KB] 获取文档内容失败: {e}")
            return None

    def format_kb_list(self, kbs: list[dict]) -> str:
        """格式化知识库列表

        Args:
            kbs: 知识库列表

        Returns:
            格式化的文本
        """
        if not kbs:
            return "暂无知识库"

        lines = [f"📚 知识库列表（{len(kbs)} 个）", ""]
        for i, kb in enumerate(kbs, 1):
            name = kb.get("book_name", "未知")
            doc_count = kb.get("doc_count", 0)
            total_words = kb.get("total_words", 0)
            lines.append(f"{i}. {name} ({doc_count} 篇, {total_words} 字)")

        lines.append("")
        lines.append("使用 /kb <知识库> 查看详情")
        return "\n".join(lines)

    def format_kb_info(self, info: dict) -> str:
        """格式化知识库概览

        Args:
            info: 知识库信息

        Returns:
            格式化的文本
        """
        book_name = info.get("book_name", "未知")
        doc_count = info.get("doc_count", 0)
        total_words = info.get("total_words", 0)
        contributors = info.get("contributors", [])
        recent_updates = info.get("recent_updates", [])
        latest_update = info.get("latest_update", "")

        lines = [f"📚 {book_name}", ""]
        lines.append(f"📄 {doc_count} 篇文档 | {len(contributors)} 位贡献者 | {total_words} 字")

        if latest_update:
            try:
                dt = datetime.fromisoformat(latest_update)
                lines.append(f"最近更新: {dt.strftime('%Y-%m-%d %H:%M')}")
            except (ValueError, TypeError):
                pass

        lines.append("")

        # 贡献者
        if contributors:
            lines.append("👥 贡献者")
            for c in contributors[:5]:
                author = c.get("author", "未知")
                doc_count = c.get("doc_count", 0)
                lines.append(f"• {author} - {doc_count} 篇")
            lines.append("")

        # 最近更新
        if recent_updates:
            lines.append("📝 最近更新")
            for i, u in enumerate(recent_updates[:5], 1):
                title = u.get("title", "未知")
                author = u.get("author", "")
                updated_at = u.get("updated_at", "")

                time_str = ""
                if updated_at:
                    try:
                        dt = datetime.fromisoformat(updated_at)
                        time_str = f" ({self._format_relative_time(dt)})"
                    except (ValueError, TypeError):
                        pass

                lines.append(f"{i}. 《{title}》- {author}{time_str}")
            lines.append("")

        lines.append("💡 使用 /kb <知识库> <问题> 在知识库内问答")
        return "\n".join(lines)

    def _format_relative_time(self, dt: datetime) -> str:
        """格式化相对时间

        Args:
            dt: 日期时间

        Returns:
            相对时间描述（如"3小时前"）
        """
        now = datetime.now()
        diff = now - dt

        if diff.days > 365:
            return f"{diff.days // 365} 年前"
        elif diff.days > 30:
            return f"{diff.days // 30} 个月前"
        elif diff.days > 0:
            return f"{diff.days} 天前"
        elif diff.seconds > 3600:
            return f"{diff.seconds // 3600} 小时前"
        elif diff.seconds > 60:
            return f"{diff.seconds // 60} 分钟前"
        else:
            return "刚刚"

    def get_kb_activity(self, book_name: str, days: int = 7) -> dict:
        """获取知识库活跃度统计

        Args:
            book_name: 知识库名称
            days: 统计天数（默认 7 天）

        Returns:
            {
                "period_days": int,
                "docs_updated": int,
                "active_contributors": [{author, doc_count}, ...],
                "total_updates": int,
            }
        """
        try:
            conn = self.doc_index._get_conn()

            # 计算起始日期
            since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

            # 本周更新的文档数
            docs_updated = conn.execute("""
                SELECT COUNT(*) as count
                FROM docs
                WHERE book_name = ? AND date(updated_at) >= date(?)
            """, (book_name, since)).fetchone()

            # 本周活跃贡献者
            active_rows = conn.execute("""
                SELECT author, COUNT(*) as doc_count
                FROM docs
                WHERE book_name = ? AND date(updated_at) >= date(?) AND author != ''
                GROUP BY author
                ORDER BY doc_count DESC
                LIMIT 10
            """, (book_name, since)).fetchall()

            docs_count = dict(docs_updated)["count"] if docs_updated else 0

            return {
                "period_days": days,
                "docs_updated": docs_count,
                "active_contributors": [dict(row) for row in active_rows],
                "since_date": since,
            }

        except Exception as e:
            logger.error(f"[KB] 获取活跃度失败: {e}")
            return {
                "period_days": days,
                "docs_updated": 0,
                "active_contributors": [],
                "since_date": "",
            }

    def get_kb_sample_docs(self, book_name: str, limit: int = 5) -> list[dict]:
        """获取知识库代表性文档（用于 Agent 分析）

        按字数排序，优先获取内容充实的文档。

        Args:
            book_name: 知识库名称
            limit: 最大返回数量

        Returns:
            [{title, author, content, word_count}, ...]
        """
        if not self.rag:
            return []

        # 先尝试检索关键文档
        keywords = ["README", "手册", "指南", "介绍", "说明", "参与", "入门", "规则", "公约", "怎么玩"]
        sample_docs = []
        seen_titles = set()

        for kw in keywords:
            if len(sample_docs) >= limit:
                break
            results = self.search_in_kb(book_name, kw, k=2)
            for r in results:
                title = r.get("title", "")
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    sample_docs.append(r)

        # 如果不够，补充字数最多的文档
        if len(sample_docs) < limit:
            try:
                conn = self.doc_index._get_conn()
                rows = conn.execute("""
                    SELECT title, author, word_count
                    FROM docs
                    WHERE book_name = ? AND word_count > 100
                    ORDER BY word_count DESC
                    LIMIT ?
                """, (book_name, limit - len(sample_docs))).fetchall()

                for row in rows:
                    if row["title"] not in seen_titles:
                        # 通过 RAG 获取内容
                        results = self.search_in_kb(book_name, row["title"], k=1)
                        if results:
                            sample_docs.append(results[0])
                            seen_titles.add(row["title"])
            except Exception as e:
                logger.warning(f"[KB] 获取补充文档失败: {e}")

        return sample_docs[:limit]

    async def get_kb_guide(
        self,
        book_name: str,
        context: "Context",
        event: Any = None,
        token_monitor: Optional["TokenMonitor"] = None,
    ) -> Optional[dict]:
        """生成知识库新人导航

        返回统计数据 + Agent 生成的总结。

        Args:
            book_name: 知识库名称
            context: AstrBot Context（用于调用 Agent）
            event: 消息事件（用于 Agent 调用）
            token_monitor: Token 监控器（可选）

        Returns:
            {
                book_name: str,
                doc_count: int,
                total_words: int,
                contributors: list,
                recent_updates: list,
                activity: dict,
                sample_docs: list,
                agent_summary: str,  # Agent 生成的总结
            }
        """
        # 1. 获取基础信息
        info = self.get_kb_info(book_name)
        if not info:
            return None

        book_name_actual = info.get("book_name", book_name)

        # 2. 获取活跃度统计
        activity = self.get_kb_activity(book_name_actual, days=7)

        # 3. 获取代表性文档
        sample_docs = self.get_kb_sample_docs(book_name_actual, limit=5)

        # 4. 调用 Agent 生成总结
        agent_summary = await self._call_agent_for_summary(
            context=context,
            event=event,
            book_name=book_name_actual,
            info=info,
            activity=activity,
            sample_docs=sample_docs,
            token_monitor=token_monitor,
        )

        return {
            "book_name": book_name_actual,
            "doc_count": info.get("doc_count", 0),
            "total_words": info.get("total_words", 0),
            "contributors": info.get("contributors", []),
            "recent_updates": info.get("recent_updates", []),
            "latest_update": info.get("latest_update", ""),
            "activity": activity,
            "sample_docs": sample_docs,
            "agent_summary": agent_summary,
        }

    async def _call_agent_for_summary(
        self,
        context: "Context",
        event: Any,
        book_name: str,
        info: dict,
        activity: dict,
        sample_docs: list[dict],
        token_monitor: Optional["TokenMonitor"] = None,
    ) -> str:
        """调用 Agent 生成知识库总结

        Args:
            context: AstrBot Context
            event: 消息事件
            book_name: 知识库名称
            info: 基础信息
            activity: 活跃度统计
            sample_docs: 代表性文档
            token_monitor: Token 监控器

        Returns:
            Agent 生成的总结文本
        """
        from astrbot.core.agent.tool import ToolSet

        from .tools.kb_guide_tool import (
            GetKBInfoTool,
            GetKBStructureTool,
            ReadDocTool,
            SearchKBTool,
        )

        # 构建 prompt
        prompt = f"""请分析知识库「{book_name}」，为新人生成详细的导航报告。

## 你需要完成的任务

### 1. 这个组在做什么（2-3 句）
- 知识库主题和目标
- 当前进展或阶段
- 主要活动形式

### 2. 怎么参与（如有信息）
- 参与方式、入群/加入方法
- 活动时间（如周会、评议等）
- 联系人

### 3. 核心文档推荐（2-3 个）
- 推荐新人先看哪些文档
- 简要说明为什么推荐

### 4. 当前热点（可选）
- 近期讨论/正在进行的事项

## 你可以使用的工具

- get_kb_info: 获取知识库详细信息（文档数、贡献者等）
- get_kb_structure: 获取知识库目录结构（分区、所有文档标题）
- read_doc: 读取指定文档的详细内容
- search_in_kb: 在知识库范围内搜索关键词

## 输出格式

请按以下格式输出：

🎯 这个组在做什么
（2-3 句话，包含主题、目标、进展）

💡 怎么参与
（参与方式、活动时间、联系人等）

📖 核心文档
• 《文档名》- 简要说明
• 《文档名》- 简要说明

## 注意事项

- 先用 get_kb_structure 查看知识库有哪些文档，了解整体结构
- 用 read_doc 阅读重要文档（如 README、手册、指南、参与规则等）
- 用 search_in_kb 搜索"参与"、"规则"、"手册"、"指南"等关键词
- 如果找不到参与方式，可以建议"联系贡献者 XXX 了解"
- 不要编造不存在的活动或规则
- 内容要具体，避免空泛的描述
"""

        try:
            # 创建工具实例
            tools = [GetKBInfoTool(), GetKBStructureTool(), ReadDocTool(), SearchKBTool()]
            for tool in tools:
                tool.kb_manager = self
                tool.book_name = book_name

            # 获取 Provider ID
            prov_id = None
            if event:
                try:
                    prov_id = await context.get_current_chat_provider_id(event.unified_msg_origin)
                except Exception:
                    pass

            if not prov_id:
                # 回退：直接用 LLM 生成，不用 Agent
                prov = context.get_using_provider()
                if not prov:
                    return "🎯 这个组在做什么\n暂无信息\n\n💡 怎么参与\n暂无参与指南"

                # 简单 LLM 调用
                try:
                    resp = await prov.text_chat(
                        prompt=prompt,
                        context=[],
                        system_prompt="你是一个知识库导航助手，帮助新人快速了解知识库。请简洁、准确地回答。",
                    )
                    return resp.completion_text.strip()
                except Exception as e:
                    logger.error(f"[KB Guide] LLM 调用失败: {e}")
                    return "🎯 这个组在做什么\n暂无信息\n\n💡 怎么参与\n暂无参与指南"

            # 调用 Agent
            from astrbot.api import logger as api_logger
            api_logger.info(f"[KB Guide] 调用 Agent 分析知识库: {book_name}")

            resp = await context.tool_loop_agent(
                event=event,
                chat_provider_id=prov_id,
                prompt=prompt,
                system_prompt="你是一个知识库导航助手，帮助新人快速了解知识库。请简洁、准确地回答。",
                tools=ToolSet(tools),
                max_steps=5,
                tool_call_timeout=30,
            )

            result = resp.completion_text.strip()

            # 记录 token
            if token_monitor:
                try:
                    if hasattr(resp, "raw_completion") and resp.raw_completion:
                        usage = getattr(resp.raw_completion, "usage", None)
                        if usage:
                            token_monitor.log_usage(
                                feature="kb_guide",
                                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                            )
                except Exception as e:
                    api_logger.debug(f"[KB Guide] Token 记录失败: {e}")

            return result

        except Exception as e:
            logger.error(f"[KB Guide] Agent 调用失败: {e}", exc_info=True)
            return "🎯 这个组在做什么\n暂无信息\n\n💡 怎么参与\n暂无参与指南"

    def format_kb_guide(self, guide: dict) -> str:
        """格式化知识库导航输出

        展示统计数据 + Agent 生成的总结。

        Args:
            guide: 导航信息字典

        Returns:
            格式化的导航文本
        """
        book_name = guide.get("book_name", "未知")
        doc_count = guide.get("doc_count", 0)
        total_words = guide.get("total_words", 0)
        contributors = guide.get("contributors", [])
        recent_updates = guide.get("recent_updates", [])
        latest_update = guide.get("latest_update", "")
        activity = guide.get("activity", {})
        agent_summary = guide.get("agent_summary", "")

        lines = [f"📚 知识库：{book_name}", ""]

        # === 基本统计 ===
        lines.append("📊 基本统计")
        latest_str = ""
        if latest_update:
            try:
                dt = datetime.fromisoformat(latest_update)
                latest_str = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
        lines.append(f"• 文档数：{doc_count} 篇")
        lines.append(f"• 总字数：{total_words:,} 字")
        lines.append(f"• 贡献者：{len(contributors)} 位")
        if latest_str:
            lines.append(f"• 最新更新：{latest_str}")
        lines.append("")

        # === 贡献者 ===
        if contributors:
            lines.append("👥 贡献者")
            for c in contributors[:5]:
                author = c.get("author", "未知")
                doc_count = c.get("doc_count", 0)
                total_w = c.get("total_words", 0)
                lines.append(f"• {author} - {doc_count} 篇（{total_w:,} 字）")
            if len(contributors) > 5:
                lines.append(f"  ... 共 {len(contributors)} 位")
            lines.append("")

        # === 最近更新 ===
        if recent_updates:
            lines.append("📝 最近更新")
            for u in recent_updates[:5]:
                title = u.get("title", "未知")
                author = u.get("author", "")
                updated_at = u.get("updated_at", "")

                time_str = ""
                if updated_at:
                    try:
                        dt = datetime.fromisoformat(updated_at)
                        time_str = dt.strftime("%m.%d")
                    except (ValueError, TypeError):
                        pass

                lines.append(f"• {time_str} {author} 《{title}》")
            lines.append("")

        # === 本周活跃 ===
        if activity and activity.get("docs_updated", 0) > 0:
            lines.append("🔥 本周活跃")
            lines.append(f"• 更新 {activity['docs_updated']} 篇文档")

            active_contributors = activity.get("active_contributors", [])
            if active_contributors:
                names = [c["author"] for c in active_contributors[:5]]
                lines.append(f"• 活跃成员：{', '.join(names)}")
            lines.append("")

        # === Agent 生成的总结 ===
        if agent_summary:
            lines.append(agent_summary)
        else:
            lines.append("🎯 这个组在做什么")
            lines.append("暂无信息")
            lines.append("")
            lines.append("💡 怎么参与")
            lines.append("暂无参与指南")

        return "\n".join(lines)

    def format_kb_updates(self, book_name: str, days: int = 7) -> str:
        """格式化知识库更新感知输出

        Args:
            book_name: 知识库名称
            days: 统计天数

        Returns:
            格式化的更新动态文本
        """
        # 模糊匹配知识库
        books = self.doc_index.list_books()
        matched = None
        for b in books:
            if book_name.lower() in b.get("book_name", "").lower():
                matched = b
                break

        if not matched:
            return f"未找到知识库「{book_name}」"

        book_name_actual = matched["book_name"]

        # 获取活跃度统计
        activity = self.get_kb_activity(book_name_actual, days)

        # 获取最近更新
        updates = self.get_kb_recent_updates(book_name_actual, limit=20)

        # 计算日期范围
        since_date = activity.get("since_date", "")
        today = datetime.now().strftime("%m.%d")

        lines = [f"📚 {book_name_actual} · 近 {days} 天动态（{since_date} ~ {today}）", ""]

        # === 更新记录 ===
        docs_updated = activity.get("docs_updated", 0)
        lines.append(f"📝 更新记录（{docs_updated} 篇）")

        if updates:
            for u in updates[:15]:
                title = u.get("title", "未知")
                author = u.get("author", "")
                updated_at = u.get("updated_at", "")

                time_str = ""
                if updated_at:
                    try:
                        dt = datetime.fromisoformat(updated_at)
                        time_str = dt.strftime("%m.%d")
                    except (ValueError, TypeError):
                        pass

                lines.append(f"• {time_str} {author} 《{title}》")

            if len(updates) > 15:
                lines.append(f"... 共 {docs_updated} 篇")
        else:
            lines.append("（暂无更新）")

        lines.append("")

        # === 活跃贡献者 ===
        active_contributors = activity.get("active_contributors", [])
        if active_contributors:
            lines.append("👥 活跃贡献者")
            for c in active_contributors[:5]:
                author = c.get("author", "未知")
                doc_count = c.get("doc_count", 0)
                lines.append(f"• {author} - 更新 {doc_count} 篇")
            if len(active_contributors) > 5:
                lines.append(f"  ... 共 {len(active_contributors)} 位")
            lines.append("")

        # === 提示 ===
        lines.append("💡 提示：使用 /kb <知识库> <问题> 在知识库内问答")

        return "\n".join(lines)