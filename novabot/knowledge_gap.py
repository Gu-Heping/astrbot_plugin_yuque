"""
NovaBot 知识缺口分析
直接分析文档分布，识别知识盲区
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger

from .doc_index import DocIndex


# 预设的技术领域关键词映射
DOMAIN_KEYWORDS = {
    "Python": ["python", "py", "pip", "venv", "virtualenv", "django", "flask", "fastapi"],
    "爬虫": ["爬虫", "spider", "crawler", "scrapy", "requests", "selenium", "beautifulsoup"],
    "机器学习": ["机器学习", "ml", "machine learning", "sklearn", "scikit-learn", "模型训练"],
    "深度学习": ["深度学习", "dl", "deep learning", "神经网络", "pytorch", "tensorflow", "keras", "cnn", "rnn", "gan"],
    "LLM/AI": ["llm", "ai", "gpt", "chatgpt", "agent", "提示词", "prompt", "大模型", "claude", "embedding", "rag"],
    "前端": ["前端", "react", "vue", "html", "css", "javascript", "js", "ts", "typescript", "webpack", "vite", "node"],
    "后端": ["后端", "django", "flask", "fastapi", "api", "restful", "数据库", "mysql", "postgresql", "redis", "mongodb"],
    "Git": ["git", "github", "版本控制", "commit", "branch", "merge", "pull request", "pr"],
    "Docker/运维": ["docker", "容器", "k8s", "kubernetes", "运维", "linux", "shell", "bash", "nginx", "部署"],
    "算法": ["算法", "algorithm", "排序", "搜索", "动态规划", "贪心", "递归", "leetcode", "复杂度"],
    "数据分析": ["数据分析", "pandas", "numpy", "可视化", "matplotlib", "seaborn", "excel", "表格"],
    "Java": ["java", "spring", "jvm", "maven", "gradle", "mybatis", "tomcat"],
    "Go": ["go", "golang", "goroutine", "channel", "go mod"],
    "Rust": ["rust", "cargo", "rustc"],
    "C/C++": ["c++", "cpp", "c语言", "指针", "内存", "gcc", "clang"],
    "移动开发": ["android", "ios", "flutter", "react native", "kotlin", "swift", "移动端"],
    "游戏开发": ["游戏", "game", "unity", "unreal", "godot", "游戏开发"],
    "数学/建模": ["数学", "建模", "数学建模", "线性代数", "概率", "统计", "微积分", "matlab"],
    "学术写作": ["论文", "学术", "写作", "latex", "文献", "引用", "答辩"],
    "产品/运营": ["产品", "运营", "pm", "用户研究", "需求", "axure", "原型"],
}


class KnowledgeGapAnalyzer:
    """知识缺口分析器

    直接分析文档分布，识别：
    - 哪些技术领域文档不足
    - 哪些领域完全没有覆盖
    - 知识补充建议
    """

    def __init__(self, data_dir: Path, docs_dir: Path):
        self.data_dir = Path(data_dir)
        self.docs_dir = Path(docs_dir)
        self.doc_index = DocIndex(self.data_dir / "doc_index.db")

    def analyze_gaps(self) -> dict:
        """分析知识缺口

        Returns:
            {
                "domain_coverage": dict,
                "gaps": list[dict],
                "doc_stats": dict,
                "analysis_time": str,
            }
        """
        result = {
            "domain_coverage": {},
            "gaps": [],
            "doc_stats": {},
            "analysis_time": datetime.now().isoformat(),
        }

        # 获取文档统计
        result["doc_stats"] = self.doc_index.get_stats()

        # 分析各领域覆盖情况
        coverage = self._analyze_domain_coverage()
        result["domain_coverage"] = coverage

        # 识别缺口
        result["gaps"] = self._identify_gaps(coverage)

        return result

    def _analyze_domain_coverage(self) -> dict:
        """分析各技术领域的文档覆盖情况

        Returns:
            {domain: {"doc_count": int, "word_count": int, "docs": list}}
        """
        coverage = {}

        try:
            # 获取所有文档标题
            conn = self.doc_index._get_conn()
            rows = conn.execute("""
                SELECT title, word_count, book_name, author
                FROM docs
                WHERE title != ''
            """).fetchall()

            for row in rows:
                title = row["title"] or ""
                word_count = row["word_count"] or 0
                book_name = row["book_name"] or ""

                # 检查标题是否属于某个领域
                title_lower = title.lower()
                for domain, keywords in DOMAIN_KEYWORDS.items():
                    for kw in keywords:
                        if kw.lower() in title_lower:
                            if domain not in coverage:
                                coverage[domain] = {
                                    "doc_count": 0,
                                    "word_count": 0,
                                    "docs": []
                                }
                            coverage[domain]["doc_count"] += 1
                            coverage[domain]["word_count"] += word_count
                            coverage[domain]["docs"].append({
                                "title": title,
                                "book": book_name,
                                "words": word_count
                            })
                            break  # 一个文档只属于一个领域

            # 按文档数排序
            coverage = dict(sorted(
                coverage.items(),
                key=lambda x: x[1]["doc_count"],
                reverse=True
            ))

        except Exception as e:
            logger.error(f"[GapAnalyzer] 分析领域覆盖失败: {e}")

        return coverage

    def _identify_gaps(self, coverage: dict) -> list[dict]:
        """识别知识缺口

        Args:
            coverage: 领域覆盖数据

        Returns:
            [{"domain": str, "status": str, "priority": str, "reason": str}, ...]
        """
        gaps = []
        total_docs = sum(c["doc_count"] for c in coverage.values())

        for domain in DOMAIN_KEYWORDS.keys():
            if domain not in coverage:
                # 完全没有覆盖
                gaps.append({
                    "domain": domain,
                    "status": "缺失",
                    "priority": "高",
                    "doc_count": 0,
                    "word_count": 0,
                    "reason": "完全没有相关文档"
                })
            elif coverage[domain]["doc_count"] < 3:
                # 文档太少
                gaps.append({
                    "domain": domain,
                    "status": "不足",
                    "priority": "中",
                    "doc_count": coverage[domain]["doc_count"],
                    "word_count": coverage[domain]["word_count"],
                    "reason": f"仅有 {coverage[domain]['doc_count']} 篇文档"
                })

        # 按优先级排序
        priority_order = {"高": 0, "中": 1, "低": 2}
        gaps.sort(key=lambda x: priority_order.get(x["priority"], 3))

        return gaps

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

        stats = analysis["doc_stats"]
        lines.append("📈 文档统计")
        lines.append(f"• 总文档数：{stats['doc_count']}")
        lines.append(f"• 总字数：{stats['total_words']:,}")
        lines.append(f"• 知识库数：{stats['book_count']}")
        lines.append("")

        # 领域覆盖
        coverage = analysis["domain_coverage"]
        if coverage:
            lines.append("📚 已覆盖领域（按文档数排序）")
            for domain, data in list(coverage.items())[:10]:
                docs = data["doc_count"]
                words = data["word_count"]
                lines.append(f"• {domain}: {docs} 篇文档, {words:,} 字")
            lines.append("")

        # 缺口
        gaps = analysis["gaps"]
        if gaps:
            lines.append("🔍 知识缺口")
            for gap in gaps[:8]:
                icon = "🔴" if gap["priority"] == "高" else "🟡"
                lines.append(f"{icon} {gap['domain']} - {gap['reason']}")
            lines.append("")
            lines.append("─" * 20)
            lines.append("💡 建议：补充上述领域的文档可完善知识库覆盖")
        else:
            lines.append("✅ 知识库覆盖较完善，暂无明显缺口")

        return "\n".join(lines)


def format_gap_report(analysis: dict) -> str:
    """格式化缺口分析报告"""
    analyzer = KnowledgeGapAnalyzer(Path("."), Path("."))
    return analyzer.format_gap_report(analysis)