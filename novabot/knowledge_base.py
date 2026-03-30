"""
NovaBot 知识库层模块
以知识库为单位的服务能力
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional, Any

from astrbot.api import logger

if TYPE_CHECKING:
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

    def __init__(self, doc_index: "DocIndex", rag: Optional["RAGEngine"] = None):
        self.doc_index = doc_index
        self.rag = rag

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

    async def get_kb_guide(
        self,
        book_name: str,
        provider: Any,
        token_monitor: Optional["TokenMonitor"] = None,
    ) -> Optional[dict]:
        """生成知识库新人导航

        通过 LLM 分析知识库内容，生成适合新人阅读的导航指南。

        Args:
            book_name: 知识库名称
            provider: LLM Provider 实例
            token_monitor: Token 监控器（可选）

        Returns:
            {
                book_name: str,
                doc_count: int,
                total_words: int,
                contributors: list,
                recent_updates: list,
                guide_text: str,  # LLM 生成的导航文本
            }
        """
        from .llm_utils import call_llm
        from .prompts import KB_GUIDE_PROMPT

        # 1. 获取基础信息
        info = self.get_kb_info(book_name)
        if not info:
            return None

        book_name_actual = info.get("book_name", book_name)

        # 2. 检索代表性文档（README、手册、指南等）
        keywords = ["README", "手册", "指南", "介绍", "说明", "参与", "入门"]
        sample_docs = []
        seen_titles = set()

        for kw in keywords:
            if len(sample_docs) >= 5:
                break
            results = self.search_in_kb(book_name_actual, kw, k=2)
            for r in results:
                title = r.get("title", "")
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    sample_docs.append(r)

        # 3. 格式化输入数据
        contributors_text = self._format_contributors_for_guide(info.get("contributors", []))
        recent_updates_text = self._format_updates_for_guide(info.get("recent_updates", []))
        sample_docs_text = self._format_sample_docs_for_guide(sample_docs)

        # 4. 构建 prompt
        prompt = KB_GUIDE_PROMPT.format(
            book_name=book_name_actual,
            doc_count=info.get("doc_count", 0),
            total_words=info.get("total_words", 0),
            contributors=contributors_text,
            recent_updates=recent_updates_text,
            sample_docs=sample_docs_text,
        )

        # 5. 调用 LLM
        try:
            result = await call_llm(
                provider=provider,
                prompt=prompt,
                system_prompt="你是一个知识库导航助手，帮助新人快速了解知识库。",
                require_json=False,  # 不需要 JSON，直接返回文本
                token_monitor=token_monitor,
                feature="kb_guide",
            )

            return {
                "book_name": book_name_actual,
                "doc_count": info.get("doc_count", 0),
                "total_words": info.get("total_words", 0),
                "contributors": info.get("contributors", []),
                "recent_updates": info.get("recent_updates", []),
                "guide_text": result,
            }

        except Exception as e:
            logger.error(f"[KB] 生成导航失败: {e}")
            # 返回基础信息，不含 LLM 生成的部分
            return {
                "book_name": book_name_actual,
                "doc_count": info.get("doc_count", 0),
                "total_words": info.get("total_words", 0),
                "contributors": info.get("contributors", []),
                "recent_updates": info.get("recent_updates", []),
                "guide_text": None,
                "error": str(e),
            }

    def _format_contributors_for_guide(self, contributors: list[dict]) -> str:
        """格式化贡献者列表用于导航生成"""
        if not contributors:
            return "暂无贡献者信息"

        lines = []
        for c in contributors[:5]:
            author = c.get("author", "未知")
            doc_count = c.get("doc_count", 0)
            lines.append(f"- {author}: {doc_count} 篇文档")

        return "\n".join(lines)

    def _format_updates_for_guide(self, recent_updates: list[dict]) -> str:
        """格式化最近更新用于导航生成"""
        if not recent_updates:
            return "暂无最近更新"

        lines = []
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

            line = f"- {time_str} {author} 更新《{title}》"
            lines.append(line.strip())

        return "\n".join(lines)

    def _format_sample_docs_for_guide(self, sample_docs: list[dict]) -> str:
        """格式化代表性文档片段用于导航生成"""
        if not sample_docs:
            return "暂无代表性文档"

        lines = []
        for doc in sample_docs[:5]:
            title = doc.get("title", "未知")
            content = doc.get("content", "")[:200]  # 截取前 200 字符
            lines.append(f"### 《{title}》\n{content}...\n")

        return "\n".join(lines)

    def format_kb_guide(self, guide: dict) -> str:
        """格式化知识库导航输出

        Args:
            guide: 导航信息字典

        Returns:
            格式化的导航文本
        """
        book_name = guide.get("book_name", "未知")
        guide_text = guide.get("guide_text")
        error = guide.get("error")

        lines = [f"📚 知识库：{book_name}", ""]

        if guide_text:
            # LLM 生成的导航文本
            lines.append(guide_text)
        elif error:
            # 生成失败，显示基础信息
            lines.append("⚠️ 导航生成失败，显示基础信息：")
            lines.append("")
            lines.append(f"📄 文档数：{guide.get('doc_count', 0)}")
            lines.append(f"📝 总字数：{guide.get('total_words', 0)}")

            contributors = guide.get("contributors", [])
            if contributors:
                lines.append("")
                lines.append("👥 贡献者")
                for c in contributors[:5]:
                    author = c.get("author", "未知")
                    doc_count = c.get("doc_count", 0)
                    lines.append(f"• {author} - {doc_count} 篇")

            lines.append("")
            lines.append(f"❌ 错误：{error}")
        else:
            lines.append("暂无导航信息")

        return "\n".join(lines)