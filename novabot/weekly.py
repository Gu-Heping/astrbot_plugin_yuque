"""
NovaBot 周报生成器
基于 Git 历史生成本周活跃度报告
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger

from .git_analyzer import GitAnalyzer


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