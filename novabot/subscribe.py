"""
NovaBot 订阅管理模块
支持按知识库订阅、按作者订阅、全部订阅
支持多群、私聊推送目标
"""

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

if TYPE_CHECKING:
    from .storage import Storage


class SubscriptionManager:
    """订阅管理器

    订阅类型：
    - repo: 按知识库订阅
    - author: 按作者订阅
    - all: 全部订阅

    推送目标：
    - group: 群聊
    - private: 私聊
    """

    def __init__(self, storage: "Storage"):
        """初始化订阅管理器

        Args:
            storage: Storage 实例
        """
        self.storage = storage
        self.subscriptions_file = storage.data_dir / "subscriptions.json"

    def _load_subscriptions(self) -> dict:
        """加载订阅数据"""
        if self.subscriptions_file.exists():
            try:
                return json.loads(self.subscriptions_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.warning(f"订阅文件损坏，已重置: {e}")
                return {"subscriptions": [], "next_id": 1}
        return {"subscriptions": [], "next_id": 1}

    def _save_subscriptions(self, data: dict):
        """保存订阅数据"""
        self.subscriptions_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def subscribe(
        self,
        platform_id: str,
        umo: str,
        sub_type: str,
        target: Optional[str] = None
    ) -> tuple[bool, str]:
        """添加订阅

        Args:
            platform_id: 平台用户 ID
            umo: 会话标识（群/私聊）
            sub_type: 订阅类型 (repo / author / all)
            target: 目标（知识库名 / 作者名，all 时为空）

        Returns:
            (成功与否, 消息)
        """
        if sub_type not in ("repo", "author", "all"):
            return False, f"无效的订阅类型: {sub_type}"

        if sub_type != "all" and not target:
            return False, f"订阅类型 {sub_type} 需要指定目标"

        data = self._load_subscriptions()

        # 检查是否已存在相同订阅
        for sub in data["subscriptions"]:
            if (sub["platform_id"] == platform_id and
                sub["umo"] == umo and
                sub["sub_type"] == sub_type and
                sub.get("target") == target):
                return False, "您已订阅此项"

        # 添加新订阅
        sub_id = data["next_id"]
        data["subscriptions"].append({
            "id": sub_id,
            "platform_id": platform_id,
            "umo": umo,
            "sub_type": sub_type,
            "target": target,
            "created_at": datetime.now().isoformat(),
        })
        data["next_id"] = sub_id + 1
        self._save_subscriptions(data)

        logger.info(f"[Subscribe] {platform_id} 订阅 {sub_type}: {target or 'all'}")
        return True, f"订阅成功 (ID: {sub_id})"

    def unsubscribe(
        self,
        platform_id: str,
        umo: str,
        sub_id: Optional[int] = None
    ) -> tuple[bool, str]:
        """取消订阅

        Args:
            platform_id: 平台用户 ID
            umo: 会话标识
            sub_id: 订阅 ID，为 None 则取消该用户在该会话的所有订阅

        Returns:
            (成功与否, 消息)
        """
        data = self._load_subscriptions()
        original_count = len(data["subscriptions"])

        if sub_id is not None:
            # 取消指定订阅
            data["subscriptions"] = [
                sub for sub in data["subscriptions"]
                if not (sub["id"] == sub_id and
                        sub["platform_id"] == platform_id and
                        sub["umo"] == umo)
            ]
        else:
            # 取消该用户在该会话的所有订阅
            data["subscriptions"] = [
                sub for sub in data["subscriptions"]
                if not (sub["platform_id"] == platform_id and sub["umo"] == umo)
            ]

        removed = original_count - len(data["subscriptions"])
        if removed == 0:
            return False, "未找到订阅"

        self._save_subscriptions(data)
        logger.info(f"[Subscribe] {platform_id} 取消订阅 {removed} 项")
        return True, f"已取消 {removed} 项订阅"

    def get_subscriptions(
        self,
        platform_id: str,
        umo: Optional[str] = None
    ) -> list[dict]:
        """获取用户订阅列表

        Args:
            platform_id: 平台用户 ID
            umo: 会话标识，为 None 则获取该用户所有订阅

        Returns:
            订阅列表
        """
        data = self._load_subscriptions()
        result = []

        for sub in data["subscriptions"]:
            if sub["platform_id"] == platform_id:
                if umo is None or sub["umo"] == umo:
                    result.append(sub)

        return result

    def get_subscribers(self, doc_info: dict) -> list[tuple[str, str]]:
        """根据文档信息匹配订阅者

        匹配逻辑：
        1. all 类型订阅 → 所有订阅者
        2. repo 类型 → book_name 匹配
        3. author 类型 → author 匹配

        Args:
            doc_info: 文档信息，包含 book_name, author 等

        Returns:
            订阅者列表 [(umo, platform_id), ...]，已去重
        """
        data = self._load_subscriptions()
        subscribers = set()

        book_name = doc_info.get("book_name", "")
        author = doc_info.get("author", "")

        for sub in data["subscriptions"]:
            matched = False

            if sub["sub_type"] == "all":
                matched = True
            elif sub["sub_type"] == "repo":
                if sub.get("target") and book_name:
                    matched = sub["target"].lower() == book_name.lower()
            elif sub["sub_type"] == "author":
                if sub.get("target") and author:
                    matched = sub["target"].lower() == author.lower()

            if matched:
                subscribers.add((sub["umo"], sub["platform_id"]))

        return list(subscribers)

    def get_all_subscriptions(self) -> list[dict]:
        """获取所有订阅（用于管理）"""
        data = self._load_subscriptions()
        return data["subscriptions"]


def format_subscription_list(subscriptions: list[dict]) -> str:
    """格式化订阅列表

    Args:
        subscriptions: 订阅列表

    Returns:
        格式化的文本
    """
    if not subscriptions:
        return "您暂无订阅\n\n使用 /subscribe repo <知识库名> 订阅知识库\n使用 /subscribe author <作者名> 订阅作者\n使用 /subscribe all 订阅全部更新"

    lines = ["📋 我的订阅", ""]

    type_names = {
        "repo": "知识库",
        "author": "作者",
        "all": "全部"
    }

    for sub in subscriptions:
        sub_id = sub.get("id", "?")
        sub_type = sub.get("sub_type", "unknown")
        target = sub.get("target", "")
        created = sub.get("created_at", "")

        type_name = type_names.get(sub_type, sub_type)
        if sub_type == "all":
            lines.append(f"#{sub_id} 📌 {type_name}")
        else:
            lines.append(f"#{sub_id} 📌 {type_name}: {target}")

        if created:
            try:
                dt = datetime.fromisoformat(created)
                lines.append(f"   订阅于 {dt.strftime('%Y-%m-%d %H:%M')}")
            except:
                pass
        lines.append("")

    lines.append("使用 /unsubscribe <ID> 取消订阅")
    return "\n".join(lines)