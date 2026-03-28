"""
NovaBot 周报生成器
基于 Git 历史生成本周活跃度报告
"""

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from astrbot.api import logger

from .git_analyzer import GitAnalyzer

if TYPE_CHECKING:
    from .token_monitor import TokenMonitor


class WeeklyReporter:
    """周报生成器

    基于 Git commit 历史生成每周活跃度报告，包括：
    - 热门文档排行
    - 活跃作者排行
    - 知识趋势分析
    """

    def __init__(self, docs_dir: Path):
        self.docs_dir = Path(docs_dir)
        self.analyzer = GitAnalyzer(self.docs_dir)

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

        # 1. 获取原始统计数据
        activity = self.analyzer.get_weekly_activity()

        if activity["total_commits"] == 0:
            return "📊 本周暂无知识库更新"

        ranking = self.analyzer.get_contribution_ranking(7)

        # 2. 准备 LLM 输入
        hot_docs_text = self._format_hot_docs(activity["hot_files"])
        authors_text = self._format_authors_for_llm(activity["active_authors"])

        total_additions = sum(r.get("additions", 0) for r in ranking)
        total_deletions = sum(r.get("deletions", 0) for r in ranking)

        # 3. 调用 LLM
        try:
            llm_result = await call_llm(
                provider=provider,
                prompt=WEEKLY_INSIGHT_PROMPT.format(
                    period=activity["period"],
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

        # 4. 组装最终周报
        return self._format_final_report(activity, ranking, llm_result)

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

    def _format_final_report(
        self,
        activity: dict,
        ranking: list[dict],
        llm_result: dict,
    ) -> str:
        """格式化最终周报

        Args:
            activity: 活跃度数据
            ranking: 贡献排行
            llm_result: LLM 分析结果

        Returns:
            格式化的周报文本
        """
        lines = []
        lines.append("📊 NOVA 本周知识周报")
        lines.append(f"📅 统计周期：{activity['period']}")
        lines.append("")

        # 本周概况
        total_additions = sum(r.get("additions", 0) for r in ranking)
        total_deletions = sum(r.get("deletions", 0) for r in ranking)
        author_count = len(activity["active_authors"])

        lines.append("📝 本周概况")
        lines.append(
            f"总提交 {activity['total_commits']} 次，"
            f"{author_count} 位作者贡献了 {len(activity['hot_files'])} 篇文档更新"
        )
        lines.append(f"+{total_additions} / -{total_deletions} 行变更")
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

        # 热门文档 TOP 5
        if activity["hot_files"]:
            lines.append("🔥 热门文档 TOP 5")
            for i, item in enumerate(activity["hot_files"][:5], 1):
                file_name = self._extract_file_name(item["file"])
                lines.append(f"{i}. 《{file_name}》- {item['changes']} 次更新")
            lines.append("")

        # 活跃作者
        if activity["active_authors"]:
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