"""
NovaBot 周报生成器
基于 Git 历史 + 文档元数据生成本周活跃度报告
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from astrbot.api import logger

from .git_analyzer import GitAnalyzer

if TYPE_CHECKING:
    from .doc_index import DocIndex
    from .token_monitor import TokenMonitor


class WeeklyReporter:
    """周报生成器

    使用双数据源：
    - SQLite 元数据：新建/更新文档、作者、知识库、字数
    - Git 历史：活跃时间、commit 数量、代码变更行数
    """

    def __init__(self, docs_dir: Path, doc_index: Optional["DocIndex"] = None):
        self.docs_dir = Path(docs_dir)
        self.doc_index = doc_index
        self.analyzer = GitAnalyzer(self.docs_dir)

    def _get_week_date_range(self) -> tuple[str, str]:
        """获取本周日期范围

        Returns:
            (since_date, until_date) 格式为 YYYY-MM-DD
        """
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        since = monday.strftime("%Y-%m-%d")
        until = today.strftime("%Y-%m-%d")
        return since, until

    def generate_weekly_report(self) -> str:
        """生成周报

        Returns:
            格式化的周报文本
        """
        # 获取本周活跃度
        activity = self.analyzer.get_weekly_activity()

        if activity["total_commits"] == 0:
            return "📊 本周暂无知识库更新"

        lines = []
        lines.append("📊 NOVA 本周知识周报")
        lines.append(f"📅 统计周期：{activity['period']}")
        lines.append(f"📝 总提交数：{activity['total_commits']}")
        lines.append("")

        # 热门文档 TOP 5
        if activity["hot_files"]:
            lines.append("🔥 热门文档 TOP 5（按修改次数）")
            for i, item in enumerate(activity["hot_files"][:5], 1):
                file_name = self._extract_file_name(item["file"])
                lines.append(f"{i}. 《{file_name}》({item['changes']} 次修改)")
            lines.append("")

        # 活跃作者
        if activity["active_authors"]:
            lines.append("✍️ 活跃作者（按提交数）")
            for author_info in activity["active_authors"][:10]:
                # 获取详细统计
                stats = self.analyzer.get_author_stats(author_info["author"], 7)
                additions = stats["additions"]
                deletions = stats["deletions"]
                lines.append(
                    f"• {author_info['author']} - {author_info['commits']} 次提交, "
                    f"+{additions}/-{deletions} 行"
                )
            lines.append("")

        # 贡献排行（按变更量）
        ranking = self.analyzer.get_contribution_ranking(7)
        if ranking:
            lines.append("📈 贡献排行（按变更量）")
            for i, item in enumerate(ranking[:5], 1):
                total_changes = item["additions"] + item["deletions"]
                lines.append(
                    f"{i}. {item['author']} - "
                    f"+{item['additions']}/-{item['deletions']} 行 "
                    f"({item['commits']} commits)"
                )
            lines.append("")

        # 知识趋势（检测热点关键词）
        trends = self._detect_trends()
        if trends:
            lines.append("📈 知识趋势")
            for trend in trends[:3]:
                lines.append(f"• {trend['topic']} - {trend['description']}")
            lines.append("")

        lines.append("─" * 20)
        lines.append(f"🤖 由 NovaBot 自动生成 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

        return "\n".join(lines)

    async def generate_weekly_report_with_llm(
        self,
        provider: Any,
        token_monitor: Optional["TokenMonitor"] = None,
    ) -> str:
        """生成带 LLM 分析的周报

        Args:
            provider: LLM Provider 实例
            token_monitor: Token 监控器（可选）

        Returns:
            格式化的周报文本
        """
        from .llm_utils import call_llm
        from .prompts.weekly import WEEKLY_INSIGHT_PROMPT

        since, until = self._get_week_date_range()
        period = f"{since} ~ {until}"

        # 1. 从 SQLite 获取文档统计（主要数据源）
        doc_stats = None
        if self.doc_index:
            try:
                doc_stats = self.doc_index.get_weekly_stats(since)
            except Exception as e:
                logger.warning(f"[Weekly] 获取文档统计失败: {e}")

        # 2. 从 Git 获取活跃度和变更
        activity = self.analyzer.get_weekly_activity()
        ranking = self.analyzer.get_contribution_ranking(7)

        # 3. 判断是否有数据
        has_sqlite_data = doc_stats and (doc_stats["total_new"] > 0 or doc_stats["total_updated"] > 0)
        has_git_data = activity["total_commits"] > 0

        if not has_sqlite_data and not has_git_data:
            return "📊 本周暂无知识库更新"

        # 4. 准备 LLM 输入
        if doc_stats:
            # 使用 SQLite 数据
            hot_docs_text = self._format_sqlite_docs(doc_stats)
            authors_text = self._format_sqlite_authors(doc_stats)
            total_new = doc_stats["total_new"]
            total_updated = doc_stats["total_updated"]
            total_words = doc_stats["total_words_new"]
        else:
            # 回退到 Git 数据
            hot_docs_text = self._format_hot_docs(activity["hot_files"])
            authors_text = self._format_authors_for_llm(activity["active_authors"])
            total_new = 0
            total_updated = len(activity["hot_files"])
            total_words = 0

        total_additions = sum(r.get("additions", 0) for r in ranking) if ranking else 0
        total_deletions = sum(r.get("deletions", 0) for r in ranking) if ranking else 0

        # 5. 调用 LLM
        try:
            llm_result = await call_llm(
                provider=provider,
                prompt=WEEKLY_INSIGHT_PROMPT.format(
                    period=period,
                    hot_docs=hot_docs_text,
                    active_authors=authors_text,
                    total_commits=activity["total_commits"],
                    total_additions=total_additions,
                    total_deletions=total_deletions,
                ),
                token_monitor=token_monitor,
                feature="weekly",
            )
        except Exception as e:
            logger.warning(f"[Weekly] LLM 分析失败，回退到纯统计: {e}")
            return self.generate_weekly_report()

        # 6. 组装最终周报
        return self._format_final_report_v2(
            period=period,
            doc_stats=doc_stats,
            activity=activity,
            ranking=ranking,
            llm_result=llm_result,
        )

    def _format_sqlite_docs(self, doc_stats: dict) -> str:
        """格式化 SQLite 文档列表（用于 LLM 输入）"""
        lines = []

        new_docs = doc_stats.get("new_docs", [])
        if new_docs:
            lines.append("### 新建文档")
            for doc in new_docs[:10]:
                lines.append(f"- 《{doc['title']}》by {doc['author']} [{doc['book_name']}]")

        updated_docs = doc_stats.get("updated_docs", [])
        if updated_docs:
            lines.append("### 更新文档")
            for doc in updated_docs[:10]:
                lines.append(f"- 《{doc['title']}》by {doc['author']} [{doc['book_name']}]")

        return "\n".join(lines) if lines else "暂无"

    def _format_sqlite_authors(self, doc_stats: dict) -> str:
        """格式化 SQLite 作者列表（用于 LLM 输入）"""
        author_stats = doc_stats.get("author_stats", [])
        if not author_stats:
            return "暂无"

        lines = []
        for author in author_stats[:10]:
            lines.append(f"- {author['author']} ({author['doc_count']} 篇文档, {author['total_words']} 字)")

        return "\n".join(lines)

    def _format_hot_docs(self, hot_files: list[dict], max_count: int = 10) -> str:
        """格式化热门文档列表（用于 LLM 输入）

        Args:
            hot_files: 热门文件列表
            max_count: 最大数量

        Returns:
            格式化的文本
        """
        if not hot_files:
            return "暂无"

        lines = []
        for item in hot_files[:max_count]:
            file_name = self._extract_file_name(item["file"])
            lines.append(f"- 《{file_name}》({item['changes']} 次修改)")

        return "\n".join(lines)

    def _format_authors_for_llm(self, active_authors: list[dict], max_count: int = 10) -> str:
        """格式化活跃作者列表（用于 LLM 输入）

        Args:
            active_authors: 活跃作者列表
            max_count: 最大数量

        Returns:
            格式化的文本
        """
        if not active_authors:
            return "暂无"

        lines = []
        for author_info in active_authors[:max_count]:
            lines.append(f"- {author_info['author']} ({author_info['commits']} 次提交)")

        return "\n".join(lines)

    def _format_final_report_v2(
        self,
        period: str,
        doc_stats: Optional[dict],
        activity: dict,
        ranking: list[dict],
        llm_result: dict,
    ) -> str:
        """格式化最终周报（双数据源版本）

        Args:
            period: 统计周期
            doc_stats: SQLite 文档统计
            activity: Git 活跃度数据
            ranking: Git 贡献排行
            llm_result: LLM 分析结果

        Returns:
            格式化的周报文本
        """
        lines = []
        lines.append("📊 NOVA 本周知识周报")
        lines.append(f"📅 统计周期：{period}")
        lines.append("")

        # 本周概况（合并数据）
        lines.append("📝 本周概况")

        if doc_stats:
            total_new = doc_stats.get("total_new", 0)
            total_updated = doc_stats.get("total_updated", 0)
            total_words = doc_stats.get("total_words_new", 0)
            author_count = len(doc_stats.get("author_stats", []))

            lines.append(
                f"新建 {total_new} 篇文档，更新 {total_updated} 篇文档"
            )
            lines.append(
                f"{author_count} 位作者贡献了 {total_words} 字内容"
            )
        else:
            author_count = len(activity.get("active_authors", []))
            lines.append(f"{author_count} 位作者活跃")

        # Git 数据（如果有）
        if activity.get("total_commits", 0) > 0:
            total_additions = sum(r.get("additions", 0) for r in ranking) if ranking else 0
            total_deletions = sum(r.get("deletions", 0) for r in ranking) if ranking else 0
            lines.append(
                f"{activity['total_commits']} 次 commit，+{total_additions}/-{total_deletions} 行变更"
            )

        lines.append("")

        # LLM 分析结果
        if llm_result:
            # 本周主题
            theme = llm_result.get("weekly_theme", "")
            if theme:
                lines.append(f"🎯 本周主题：{theme}")
                lines.append("")

            # 主题洞察
            insights = llm_result.get("insights", "")
            if insights:
                lines.append("📈 主题洞察")
                lines.append(insights)
                lines.append("")

            # 热点话题
            hot_topics = llm_result.get("hot_topics", [])
            if hot_topics:
                lines.append("🔥 热点话题")
                lines.append("、".join(hot_topics))
                lines.append("")

            # 下周建议
            suggestions = llm_result.get("suggestions", [])
            if suggestions:
                lines.append("💡 下周建议")
                for suggestion in suggestions:
                    lines.append(f"• {suggestion}")
                lines.append("")

        # 热门文档
        if doc_stats:
            new_docs = doc_stats.get("new_docs", [])
            updated_docs = doc_stats.get("updated_docs", [])

            if new_docs or updated_docs:
                lines.append("🔥 本周文档")

                # 新建文档按字数排序（优先显示字数多的）
                sorted_new = sorted(new_docs, key=lambda x: x.get("word_count", 0), reverse=True)[:5]

                # 更新文档按更新时间排序（最新的在前）
                sorted_updated = sorted(updated_docs, key=lambda x: x.get("updated_at", ""), reverse=True)[:5]

                # 合并显示（新建优先）
                for i, doc in enumerate(sorted_new, 1):
                    lines.append(f"{i}. 《{doc['title']}》- 新建，{doc['word_count']} 字")
                for i, doc in enumerate(sorted_updated, len(sorted_new) + 1):
                    lines.append(f"{i}. 《{doc['title']}》- 更新")
                lines.append("")
        elif activity.get("hot_files"):
            lines.append("🔥 热门文档 TOP 5")
            for i, item in enumerate(activity["hot_files"][:5], 1):
                file_name = self._extract_file_name(item["file"])
                lines.append(f"{i}. 《{file_name}》- {item['changes']} 次更新")
            lines.append("")

        # 活跃作者
        if doc_stats and doc_stats.get("author_stats"):
            lines.append("✍️ 活跃作者")
            for author in doc_stats["author_stats"][:5]:
                lines.append(
                    f"• {author['author']} - "
                    f"{author['doc_count']} 篇文档, {author['total_words']} 字"
                )
            lines.append("")
        elif activity.get("active_authors"):
            lines.append("✍️ 活跃作者")
            for author_info in activity["active_authors"][:5]:
                stats = self.analyzer.get_author_stats(author_info["author"], 7)
                lines.append(
                    f"• {author_info['author']} - "
                    f"{author_info['commits']} 次提交, "
                    f"+{stats['additions']}/-{stats['deletions']}"
                )
            lines.append("")

        lines.append("─" * 20)
        lines.append(f"🤖 由 NovaBot 自动生成 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

        return "\n".join(lines)

    def _extract_file_name(self, file_path: str) -> str:
        """从文件路径提取文档名称

        Args:
            file_path: 文件路径

        Returns:
            文档名称（去除扩展名和路径）
        """
        import os
        name = os.path.basename(file_path)
        if name.endswith(".md"):
            name = name[:-3]
        return name

    def _detect_trends(self) -> list[dict]:
        """检测知识趋势

        Returns:
            [{"topic": str, "description": str}, ...]
        """
        trends = []

        # 定义关注的主题关键词
        topics = [
            ("AI", "AI/机器学习"),
            ("Agent", "AI Agent"),
            ("爬虫", "爬虫开发"),
            ("LLM", "LLM 应用"),
            ("Python", "Python 开发"),
            ("React", "前端开发"),
            ("Vue", "前端开发"),
            ("Docker", "容器技术"),
            ("Git", "版本控制"),
            ("算法", "算法学习"),
        ]

        for pattern, topic_name in topics:
            trend = self.analyzer.get_trend(pattern, 7)
            if trend["total_commits"] >= 2:
                desc = f"{trend['total_commits']} 次提交，{trend['files_matched']} 个文件"
                trends.append({
                    "topic": topic_name,
                    "description": desc,
                    "commits": trend["total_commits"],
                })

        # 按提交数排序
        trends.sort(key=lambda x: x["commits"], reverse=True)
        return trends


def format_weekly_report(report: str) -> str:
    """格式化周报输出

    Args:
        report: 周报内容

    Returns:
        格式化后的周报
    """
    return report