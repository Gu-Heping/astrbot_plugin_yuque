"""
NovaBot 知识缺口分析
基于搜索日志和文档统计识别知识盲区
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger

from .doc_index import DocIndex
from .search_log import SearchLogger


class KnowledgeGapAnalyzer:
    """知识缺口分析器

    通过分析搜索日志和文档分布，识别：
    - 搜索频繁但文档不足的主题
    - 无结果的查询模式
    - 知识补充建议
    """

    def __init__(self, data_dir: Path, docs_dir: Path):
        self.data_dir = Path(data_dir)
        self.docs_dir = Path(docs_dir)
        self.search_logger = SearchLogger(self.data_dir)

    def analyze_gaps(self, days: int = 30) -> dict:
        """分析知识缺口

        Args:
            days: 统计天数

        Returns:
            {
                "empty_queries": list[dict],
                "suggestions": list[dict],
                "search_stats": dict,
                "analysis_time": str,
            }
        """
        result = {
            "empty_queries": [],
            "suggestions": [],
            "search_stats": {},
            "analysis_time": datetime.now().isoformat(),
        }

        # 获取搜索统计
        result["search_stats"] = self.search_logger.get_stats()

        # 获取无结果查询
        empty_queries = self.search_logger.get_empty_queries(days)
        result["empty_queries"] = empty_queries[:10]  # TOP 10

        # 生成补充建议
        result["suggestions"] = self._generate_suggestions(empty_queries)

        return result

    def _generate_suggestions(self, empty_queries: list[dict]) -> list[dict]:
        """生成知识补充建议

        Args:
            empty_queries: 无结果查询列表

        Returns:
            [{"topic": str, "reason": str, "priority": str}, ...]
        """
        suggestions = []

        # 定义关键词与知识领域的映射
        topic_keywords = {
            "Python": ["python", "py", "pip", "venv", "virtualenv"],
            "爬虫": ["爬虫", "spider", "crawler", "scrapy", "requests"],
            "机器学习": ["机器学习", "ml", "machine learning", "sklearn", "模型"],
            "深度学习": ["深度学习", "dl", "deep learning", "神经网络", "pytorch", "tensorflow"],
            "LLM/AI": ["llm", "ai", "gpt", "chatgpt", "agent", "提示词", "prompt"],
            "前端": ["前端", "react", "vue", "html", "css", "javascript", "js", "ts"],
            "后端": ["后端", "django", "flask", "fastapi", "api", "数据库"],
            "Git": ["git", "github", "版本控制", "commit", "branch"],
            "Docker": ["docker", "容器", "k8s", "kubernetes"],
            "算法": ["算法", "algorithm", "排序", "搜索", "动态规划"],
            "数据分析": ["数据分析", "pandas", "numpy", "可视化", "matplotlib"],
            "Linux": ["linux", "ubuntu", "shell", "bash", "命令行"],
        }

        # 统计每个主题的空搜索次数
        topic_counts: dict[str, int] = {}
        for entry in empty_queries:
            query = entry["query"].lower()
            for topic, keywords in topic_keywords.items():
                for kw in keywords:
                    if kw.lower() in query:
                        topic_counts[topic] = topic_counts.get(topic, 0) + entry["count"]
                        break

        # 生成建议
        for topic, count in sorted(topic_counts.items(), key=lambda x: x[1], reverse=True):
            priority = "高" if count >= 5 else ("中" if count >= 2 else "低")
            suggestions.append({
                "topic": topic,
                "reason": f"有 {count} 次相关搜索无结果",
                "priority": priority,
                "search_count": count,
            })

        return suggestions[:5]  # TOP 5 建议

    def format_gap_report(self, analysis: dict) -> str:
        """格式化缺口分析报告

        Args:
            analysis: 分析结果

        Returns:
            格式化的报告文本
        """
        lines = []
        lines.append("📊 知识缺口分析报告")
        lines.append(f"📅 分析时间：{analysis['analysis_time'][:10]}")
        lines.append("")

        stats = analysis["search_stats"]
        if stats["total_searches"] == 0:
            lines.append("暂无搜索记录，无法分析知识缺口")
            lines.append("提示：用户搜索后，系统会自动记录分析")
            return "\n".join(lines)

        lines.append("📈 搜索统计")
        lines.append(f"• 总搜索次数：{stats['total_searches']}")
        lines.append(f"• 无结果比例：{stats['empty_rate']}%")
        lines.append(f"• 唯一查询数：{stats['unique_queries']}")
        lines.append("")

        # 无结果查询
        if analysis["empty_queries"]:
            lines.append("🔍 无结果查询 TOP 5")
            for i, entry in enumerate(analysis["empty_queries"][:5], 1):
                lines.append(f"{i}. 「{entry['query']}」({entry['count']} 次)")
            lines.append("")

        # 补充建议
        if analysis["suggestions"]:
            lines.append("💡 知识补充建议")
            for sug in analysis["suggestions"]:
                priority_icon = "🔴" if sug["priority"] == "高" else ("🟡" if sug["priority"] == "中" else "🟢")
                lines.append(f"{priority_icon} {sug['topic']} - {sug['reason']}")
            lines.append("")

        lines.append("─" * 20)
        lines.append("💡 提示：补充相关文档可减少无结果搜索")

        return "\n".join(lines)


def format_gap_report(analysis: dict) -> str:
    """格式化缺口分析报告

    Args:
        analysis: 分析结果

    Returns:
        格式化的报告文本
    """
    analyzer = KnowledgeGapAnalyzer(Path("."), Path("."))
    return analyzer.format_gap_report(analysis)