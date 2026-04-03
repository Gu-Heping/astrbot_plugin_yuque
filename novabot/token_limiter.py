"""
NovaBot Token 限流器
防止 Token 消耗爆炸，支持用户级别限流
"""

import json
import threading
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from astrbot.api import logger


class TokenLimiter:
    """Token 限流器

    为每个用户设置每日 Token 使用限额，防止滥用。
    """

    # 默认配置
    DEFAULT_DAILY_LIMIT = 50000  # 每用户每日 50K tokens
    DEFAULT_WARNING_THRESHOLD = 0.8  # 80% 时警告

    def __init__(
        self,
        data_dir: Path,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
        warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    ):
        """初始化限流器

        Args:
            data_dir: 数据目录
            daily_limit: 每日限额
            warning_threshold: 警告阈值（0-1）
        """
        self.data_dir = Path(data_dir)
        self.usage_file = self.data_dir / "token_usage_by_user.json"
        self.daily_limit = daily_limit
        self.warning_threshold = warning_threshold

        # 并发锁
        self._lock = threading.Lock()

        # 用户使用记录 {user_id: {date: "2026-04-03", used: 12345}}
        self._usage: dict = {}

        # 加载数据
        self._load()

    def _load(self):
        """加载使用记录"""
        if self.usage_file.exists():
            try:
                self._usage = json.loads(self.usage_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"[TokenLimiter] 加载使用记录失败: {e}")
                self._usage = {}

    def _save(self):
        """保存使用记录"""
        try:
            self.usage_file.write_text(
                json.dumps(self._usage, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"[TokenLimiter] 保存使用记录失败: {e}")

    def check_limit(self, user_id: str) -> tuple[bool, int, int]:
        """检查用户是否超过限额

        Args:
            user_id: 用户标识

        Returns:
            (is_allowed, remaining, used)
            - is_allowed: 是否允许使用
            - remaining: 剩余额度
            - used: 已使用额度
        """
        if not user_id:
            return True, self.daily_limit, 0

        today = date.today().isoformat()

        with self._lock:
            # 获取用户今日使用量
            user_data = self._usage.get(user_id, {})
            used_date = user_data.get("date", "")

            # 如果是新的一天，重置计数
            if used_date != today:
                self._usage[user_id] = {"date": today, "used": 0}
                used = 0
            else:
                used = user_data.get("used", 0)

            remaining = self.daily_limit - used
            is_allowed = used < self.daily_limit

            return is_allowed, remaining, used

    def check_and_reserve(self, user_id: str, tokens: int) -> tuple[bool, int, int]:
        """原子操作：检查限额并预留 Token

        解决 check_limit 和 record_usage 之间的竞态条件。

        Args:
            user_id: 用户标识
            tokens: 需要预留的 Token 数

        Returns:
            (is_allowed, remaining, used)
            - is_allowed: 是否允许使用
            - remaining: 剩余额度
            - used: 已使用额度（包括本次预留）
        """
        if not user_id:
            return True, self.daily_limit, 0

        if tokens <= 0:
            return True, self.daily_limit, 0

        today = date.today().isoformat()

        with self._lock:
            # 获取用户今日使用量
            user_data = self._usage.get(user_id, {})
            used_date = user_data.get("date", "")

            # 如果是新的一天，重置计数
            if used_date != today:
                self._usage[user_id] = {"date": today, "used": 0}
                current_used = 0
            else:
                current_used = user_data.get("used", 0)

            # 检查是否超限
            if current_used + tokens > self.daily_limit:
                remaining = self.daily_limit - current_used
                logger.warning(
                    f"[TokenLimiter] 用户 {user_id} 超限: "
                    f"current={current_used}, request={tokens}, limit={self.daily_limit}"
                )
                return False, remaining, current_used

            # 预留 Token
            new_used = current_used + tokens
            self._usage[user_id]["used"] = new_used

            # 保存
            self._save()

            # 检查警告阈值
            if new_used >= self.daily_limit * self.warning_threshold:
                logger.warning(
                    f"[TokenLimiter] 用户 {user_id} 接近限额: "
                    f"{new_used}/{self.daily_limit}"
                )

            remaining = self.daily_limit - new_used
            return True, remaining, new_used

    def record_usage(self, user_id: str, tokens: int) -> bool:
        """记录用户 Token 使用

        Args:
            user_id: 用户标识
            tokens: 使用的 Token 数

        Returns:
            是否记录成功（如果超限则返回 False）
        """
        if not user_id or tokens <= 0:
            return True

        today = date.today().isoformat()

        with self._lock:
            # 获取用户今日使用量
            user_data = self._usage.get(user_id, {})
            used_date = user_data.get("date", "")

            # 如果是新的一天，重置计数
            if used_date != today:
                self._usage[user_id] = {"date": today, "used": 0}

            # 检查限额
            current_used = self._usage[user_id]["used"]
            if current_used + tokens > self.daily_limit:
                logger.warning(
                    f"[TokenLimiter] 用户 {user_id} 超限: "
                    f"current={current_used}, add={tokens}, limit={self.daily_limit}"
                )
                return False

            # 记录使用
            self._usage[user_id]["used"] = current_used + tokens

            # 检查警告阈值
            new_used = self._usage[user_id]["used"]
            if new_used >= self.daily_limit * self.warning_threshold:
                logger.warning(
                    f"[TokenLimiter] 用户 {user_id} 接近限额: "
                    f"{new_used}/{self.daily_limit}"
                )

            # 保存
            self._save()

            return True

    def get_usage(self, user_id: str) -> dict:
        """获取用户使用情况

        Args:
            user_id: 用户标识

        Returns:
            {used: int, limit: int, remaining: int, percentage: float}
        """
        if not user_id:
            return {
                "used": 0,
                "limit": self.daily_limit,
                "remaining": self.daily_limit,
                "percentage": 0,
            }

        today = date.today().isoformat()

        with self._lock:
            user_data = self._usage.get(user_id, {})
            used_date = user_data.get("date", "")

            if used_date != today:
                used = 0
            else:
                used = user_data.get("used", 0)

        remaining = max(0, self.daily_limit - used)
        percentage = used / self.daily_limit if self.daily_limit > 0 else 0

        return {
            "used": used,
            "limit": self.daily_limit,
            "remaining": remaining,
            "percentage": percentage,
        }

    def reset_user(self, user_id: str):
        """重置用户使用记录"""
        with self._lock:
            if user_id in self._usage:
                del self._usage[user_id]
                self._save()
                logger.info(f"[TokenLimiter] 重置用户 {user_id} 的使用记录")

    def get_all_usage(self) -> dict:
        """获取所有用户使用情况"""
        today = date.today().isoformat()

        with self._lock:
            result = {}
            for user_id, data in self._usage.items():
                if data.get("date") == today:
                    result[user_id] = {
                        "used": data.get("used", 0),
                        "limit": self.daily_limit,
                    }

        return result

    def cleanup_old_records(self, days: int = 7):
        """清理旧记录"""
        cutoff = date.today()
        from datetime import timedelta
        cutoff = (cutoff - timedelta(days=days)).isoformat()

        with self._lock:
            to_delete = []
            for user_id, data in self._usage.items():
                if data.get("date", "") < cutoff:
                    to_delete.append(user_id)

            for user_id in to_delete:
                del self._usage[user_id]

            if to_delete:
                self._save()
                logger.info(f"[TokenLimiter] 清理了 {len(to_delete)} 条旧记录")