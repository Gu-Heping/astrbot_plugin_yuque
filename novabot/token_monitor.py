"""
NovaBot Token 消耗监控
记录和统计 LLM API 调用的 token 使用量
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger


class TokenMonitor:
    """Token 消耗监控器

    记录每次 LLM 调用的 token 使用量，支持：
    - 按功能分类统计
    - 按时间统计
    - 总消耗概览
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.log_file = self.data_dir / "token_logs.json"
        self._logs: list[dict] = []
        self._loaded = False
        self._lock = threading.Lock()  # 并发锁保护

    def _ensure_loaded(self):
        """确保日志已加载"""
        if self._loaded:
            return

        with self._lock:
            if self._loaded:  # 双重检查
                return

            if self.log_file.exists():
                try:
                    with open(self.log_file, "r", encoding="utf-8") as f:
                        self._logs = json.load(f)
                except Exception as e:
                    logger.warning(f"[TokenMonitor] 加载日志失败: {e}")
                    self._logs = []
            self._loaded = True

    def _save(self):
        """保存日志"""
        with self._lock:
            try:
                with open(self.log_file, "w", encoding="utf-8") as f:
                    json.dump(self._logs, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"[TokenMonitor] 保存日志失败: {e}")

    def log_usage(
        self,
        feature: str,
        input_tokens: int,
        output_tokens: int,
        model: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        """记录一次 token 使用

        Args:
            feature: 功能名称（如 "profile", "knowledge_card", "push"）
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            model: 模型名称
            user_id: 用户 ID
        """
        self._ensure_loaded()

        entry = {
            "feature": feature,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "model": model,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            self._logs.append(entry)

            # 限制日志数量，保留最近 2000 条
            if len(self._logs) > 2000:
                self._logs = self._logs[-2000:]

            try:
                with open(self.log_file, "w", encoding="utf-8") as f:
                    json.dump(self._logs, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"[TokenMonitor] 保存日志失败: {e}")

    def get_stats(self, days: int = 30) -> dict:
        """获取统计信息

        Args:
            days: 统计天数

        Returns:
            {
                "total_input": int,
                "total_output": int,
                "total_tokens": int,
                "call_count": int,
                "by_feature": dict,
                "by_day": list[dict],
            }
        """
        self._ensure_loaded()

        cutoff = datetime.now().timestamp() - days * 24 * 3600

        total_input = 0
        total_output = 0
        call_count = 0
        by_feature: dict[str, dict] = {}
        by_day: dict[str, dict] = {}

        for entry in self._logs:
            ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
            if ts < cutoff:
                continue

            total_input += entry["input_tokens"]
            total_output += entry["output_tokens"]
            call_count += 1

            # 按功能统计
            feature = entry["feature"]
            if feature not in by_feature:
                by_feature[feature] = {"input": 0, "output": 0, "calls": 0}
            by_feature[feature]["input"] += entry["input_tokens"]
            by_feature[feature]["output"] += entry["output_tokens"]
            by_feature[feature]["calls"] += 1

            # 按天统计
            day = entry["timestamp"][:10]
            if day not in by_day:
                by_day[day] = {"input": 0, "output": 0, "calls": 0}
            by_day[day]["input"] += entry["input_tokens"]
            by_day[day]["output"] += entry["output_tokens"]
            by_day[day]["calls"] += 1

        # 转换 by_day 为列表
        daily_list = [
            {"date": d, **stats} for d, stats in sorted(by_day.items())
        ]

        return {
            "total_input": total_input,
            "total_output": total_output,
            "total_tokens": total_input + total_output,
            "call_count": call_count,
            "by_feature": by_feature,
            "by_day": daily_list[-7:],  # 最近 7 天
        }

    def get_feature_stats(self, feature: str, days: int = 30) -> dict:
        """获取特定功能的统计

        Args:
            feature: 功能名称
            days: 统计天数

        Returns:
            {"calls": int, "input": int, "output": int, "avg_tokens": float}
        """
        self._ensure_loaded()

        cutoff = datetime.now().timestamp() - days * 24 * 3600

        calls = 0
        input_total = 0
        output_total = 0

        for entry in self._logs:
            if entry["feature"] != feature:
                continue

            ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
            if ts < cutoff:
                continue

            calls += 1
            input_total += entry["input_tokens"]
            output_total += entry["output_tokens"]

        avg = (input_total + output_total) / calls if calls > 0 else 0

        return {
            "feature": feature,
            "calls": calls,
            "input": input_total,
            "output": output_total,
            "avg_tokens": round(avg, 1),
        }

    def format_stats_report(self, stats: dict) -> str:
        """格式化统计报告

        Args:
            stats: 统计数据

        Returns:
            格式化的报告文本
        """
        lines = []
        lines.append("📊 Token 消耗统计")
        lines.append(f"📅 统计周期：最近 30 天")
        lines.append("")

        if stats["call_count"] == 0:
            lines.append("暂无 token 使用记录")
            return "\n".join(lines)

        lines.append("📈 总消耗")
        lines.append(f"• 调用次数：{stats['call_count']}")
        lines.append(f"• 输入 Token：{stats['total_input']:,}")
        lines.append(f"• 输出 Token：{stats['total_output']:,}")
        lines.append(f"• 总 Token：{stats['total_tokens']:,}")
        lines.append("")

        # 按功能统计
        if stats["by_feature"]:
            lines.append("📂 按功能统计")
            for feature, fstats in sorted(
                stats["by_feature"].items(),
                key=lambda x: x[1]["input"] + x[1]["output"],
                reverse=True
            ):
                total = fstats["input"] + fstats["output"]
                lines.append(
                    f"• {feature}: {fstats['calls']} 次, "
                    f"{total:,} tokens (入 {fstats['input']:,} / 出 {fstats['output']:,})"
                )
            lines.append("")

        # 最近 7 天趋势
        if stats["by_day"]:
            lines.append("📅 最近 7 天")
            for day_stats in stats["by_day"]:
                day = day_stats["date"]
                total = day_stats["input"] + day_stats["output"]
                lines.append(
                    f"• {day}: {day_stats['calls']} 次, {total:,} tokens"
                )
            lines.append("")

        lines.append("─" * 20)

        return "\n".join(lines)

    def clear(self):
        """清空日志"""
        self._logs = []
        self._save()
        logger.info("[TokenMonitor] 日志已清空")


# 功能名称常量
FEATURE_PROFILE = "profile"  # 用户画像
FEATURE_ASSESS = "assess"  # 领域评估
FEATURE_KNOWLEDGE_CARD = "knowledge_card"  # 知识卡片
FEATURE_LEARNING_PATH = "learning_path"  # 学习路径
FEATURE_PARTNER = "partner"  # 伙伴推荐
FEATURE_PUSH = "push"  # 智能推送
FEATURE_CHAT = "chat"  # 普通对话
FEATURE_EMBEDDING = "embedding"  # Embedding 向量化