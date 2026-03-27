"""
NovaBot 搜索日志记录
记录用户的搜索查询，用于知识缺口分析
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger


class SearchLogger:
    """搜索日志记录器

    记录搜索查询用于分析：
    - 哪些查询无结果
    - 哪些主题搜索频繁但文档少
    - 知识缺口识别
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.log_file = self.data_dir / "search_logs.json"
        self._logs: list[dict] = []
        self._loaded = False

    def _ensure_loaded(self):
        """确保日志已加载"""
        if self._loaded:
            return

        if self.log_file.exists():
            try:
                with open(self.log_file, "r", encoding="utf-8") as f:
                    self._logs = json.load(f)
            except Exception as e:
                logger.warning(f"[SearchLogger] 加载日志失败: {e}")
                self._logs = []
        self._loaded = True

    def _save(self):
        """保存日志"""
        try:
            with open(self.log_file, "w", encoding="utf-8") as f:
                json.dump(self._logs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[SearchLogger] 保存日志失败: {e}")

    def log_search(
        self,
        query: str,
        results_count: int,
        search_type: str = "rag",
        user_id: Optional[str] = None,
    ):
        """记录一次搜索

        Args:
            query: 搜索查询
            results_count: 结果数量
            search_type: 搜索类型 (rag/grep)
            user_id: 用户 ID（可选）
        """
        self._ensure_loaded()

        entry = {
            "query": query,
            "results_count": results_count,
            "search_type": search_type,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
        }

        self._logs.append(entry)

        # 限制日志数量，保留最近 1000 条
        if len(self._logs) > 1000:
            self._logs = self._logs[-1000:]

        self._save()

    def get_empty_queries(self, days: int = 30) -> list[dict]:
        """获取无结果的查询

        Args:
            days: 统计天数

        Returns:
            [{"query": str, "count": int, "last_search": str}, ...]
        """
        self._ensure_loaded()

        cutoff = datetime.now().timestamp() - days * 24 * 3600

        # 统计无结果查询
        empty_counts: dict[str, dict] = {}

        for entry in self._logs:
            ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
            if ts < cutoff:
                continue

            if entry["results_count"] == 0:
                query = entry["query"]
                if query not in empty_counts:
                    empty_counts[query] = {
                        "query": query,
                        "count": 0,
                        "last_search": entry["timestamp"],
                    }
                empty_counts[query]["count"] += 1
                if entry["timestamp"] > empty_counts[query]["last_search"]:
                    empty_counts[query]["last_search"] = entry["timestamp"]

        # 按次数排序
        result = sorted(empty_counts.values(), key=lambda x: x["count"], reverse=True)
        return result

    def get_popular_queries(self, days: int = 30, limit: int = 20) -> list[dict]:
        """获取热门查询

        Args:
            days: 统计天数
            limit: 返回数量

        Returns:
            [{"query": str, "count": int, "avg_results": float}, ...]
        """
        self._ensure_loaded()

        cutoff = datetime.now().timestamp() - days * 24 * 3600

        query_stats: dict[str, dict] = {}

        for entry in self._logs:
            ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
            if ts < cutoff:
                continue

            query = entry["query"]
            if query not in query_stats:
                query_stats[query] = {
                    "query": query,
                    "count": 0,
                    "total_results": 0,
                }
            query_stats[query]["count"] += 1
            query_stats[query]["total_results"] += entry["results_count"]

        # 计算平均结果数
        result = []
        for stats in query_stats.values():
            avg = stats["total_results"] / stats["count"] if stats["count"] > 0 else 0
            result.append({
                "query": stats["query"],
                "count": stats["count"],
                "avg_results": round(avg, 1),
            })

        # 按次数排序
        result.sort(key=lambda x: x["count"], reverse=True)
        return result[:limit]

    def get_stats(self) -> dict:
        """获取搜索统计

        Returns:
            {
                "total_searches": int,
                "empty_rate": float,
                "unique_queries": int,
            }
        """
        self._ensure_loaded()

        total = len(self._logs)
        if total == 0:
            return {
                "total_searches": 0,
                "empty_rate": 0.0,
                "unique_queries": 0,
            }

        empty = sum(1 for e in self._logs if e["results_count"] == 0)
        unique = len(set(e["query"] for e in self._logs))

        return {
            "total_searches": total,
            "empty_rate": round(empty / total * 100, 1),
            "unique_queries": unique,
        }

    def clear(self):
        """清空日志"""
        self._logs = []
        self._save()
        logger.info("[SearchLogger] 日志已清空")