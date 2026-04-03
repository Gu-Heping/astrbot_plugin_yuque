"""
成员轨迹管理器
追踪成员的活动轨迹，解决"不知道谁在做什么"的问题
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from uuid import uuid4

from astrbot.api import logger


class MemberTrajectory:
    """成员轨迹管理器"""

    # 默认配置
    DEFAULT_MAX_EVENTS = 200  # 每成员最多保留的事件数
    DEFAULT_RETENTION_DAYS = 90  # 轨迹保留天数

    # 事件类型定义
    EVENT_TYPES = {
        "publish_doc": "发布文档",
        "update_doc": "更新文档",
        "answer_question": "回答问题",
        "ask_question": "提出问题",
        "milestone": "学习里程碑",
        "share_session": "分享会",
        "join_group": "加入兴趣组",
        "leave_group": "离开兴趣组",
    }

    def __init__(
        self,
        data_dir: Path,
        max_events: int = DEFAULT_MAX_EVENTS,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        """初始化成员轨迹管理器

        Args:
            data_dir: 数据存储目录
            max_events: 每成员最多保留的事件数
            retention_days: 轨迹保留天数
        """
        self.trajectory_dir = data_dir / "trajectories"
        self.trajectory_dir.mkdir(parents=True, exist_ok=True)

        self.max_events = max_events
        self.retention_days = retention_days

        # 并发锁（保护文件写入）
        self._lock = threading.Lock()

        # 成员缓存（懒加载）
        self._cache: Dict[str, dict] = {}
        self._cache_lock = threading.Lock()

        logger.info(
            f"[MemberTrajectory] 初始化完成: "
            f"max_events={max_events}, retention_days={retention_days}"
        )

    def _get_member_file(self, member_id: str) -> Path:
        """获取成员轨迹文件路径

        Args:
            member_id: 成员标识（语雀 ID 或用户名）

        Returns:
            成员轨迹文件路径
        """
        # 使用安全的文件名（避免特殊字符）
        safe_id = str(member_id).replace("/", "_").replace("\\", "_")
        return self.trajectory_dir / f"{safe_id}.json"

    def _load_member_trajectory(self, member_id: str) -> dict:
        """加载成员轨迹（带缓存）

        Args:
            member_id: 成员标识

        Returns:
            成员轨迹字典
        """
        # 检查缓存
        with self._cache_lock:
            if member_id in self._cache:
                return self._cache[member_id]

        # 从文件加载
        member_file = self._get_member_file(member_id)
        with self._lock:
            if member_file.exists():
                try:
                    trajectory = json.loads(member_file.read_text(encoding="utf-8"))
                    if not isinstance(trajectory, dict):
                        trajectory = self._create_empty_trajectory(member_id)
                    else:
                        trajectory = self._migrate_trajectory(trajectory)
                except json.JSONDecodeError as e:
                    logger.warning(f"[Trajectory] 成员轨迹文件损坏 ({member_id}): {e}")
                    trajectory = self._create_empty_trajectory(member_id)
            else:
                trajectory = self._create_empty_trajectory(member_id)

            # 存入缓存
            with self._cache_lock:
                self._cache[member_id] = trajectory

        return trajectory

    def _migrate_trajectory(self, trajectory: dict) -> dict:
        """迁移旧版本数据结构"""
        changed = False

        if "events" not in trajectory:
            trajectory["events"] = []
            changed = True

        if "stats" not in trajectory:
            trajectory["stats"] = {
                "total_events": 0,
                "last_active": None,
                "active_days": 0,
            }
            changed = True

        # 确保 stats 字段完整
        default_stats = {
            "total_events": 0,
            "last_active": None,
            "active_days": 0,
            "doc_count": 0,
            "answer_count": 0,
            "milestone_count": 0,
        }
        for key, default_value in default_stats.items():
            if key not in trajectory.get("stats", {}):
                trajectory.setdefault("stats", {})[key] = default_value
                changed = True

        if changed:
            trajectory["_migrated"] = True
        return trajectory

    def _save_member_trajectory(self, member_id: str, trajectory: dict):
        """保存成员轨迹

        Args:
            member_id: 成员标识
            trajectory: 成员轨迹字典
        """
        trajectory.pop("_migrated", None)
        member_file = self._get_member_file(member_id)
        with self._lock:
            member_file.write_text(
                json.dumps(trajectory, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        # 更新缓存
        with self._cache_lock:
            self._cache[member_id] = trajectory

    def _create_empty_trajectory(self, member_id: str) -> dict:
        """创建空的成员轨迹结构"""
        return {
            "member_id": member_id,
            "events": [],
            "stats": {
                "total_events": 0,
                "last_active": None,
                "active_days": 0,
                "doc_count": 0,
                "answer_count": 0,
                "milestone_count": 0,
            },
        }

    def record_event(
        self,
        member_id: str,
        event_type: str,
        title: str = "",
        description: str = "",
        related_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> str:
        """记录成员事件

        Args:
            member_id: 成员标识
            event_type: 事件类型（publish_doc, update_doc, answer_question 等）
            title: 事件标题（文档标题、问题标题等）
            description: 事件描述
            related_id: 关联 ID（文档 ID、问题 ID 等）
            metadata: 附加元数据
            timestamp: 事件时间戳（可选，默认当前时间）

        Returns:
            事件 ID
        """
        if not member_id or not event_type:
            logger.warning("[Trajectory] 无效参数，跳过记录")
            return ""

        # 验证事件类型
        if event_type not in self.EVENT_TYPES:
            logger.warning(f"[Trajectory] 未知事件类型: {event_type}")
            event_type = "unknown"

        # 加载轨迹
        trajectory = self._load_member_trajectory(member_id)

        # 生成事件 ID
        event_id = str(uuid4())[:8]
        # 使用传入的时间戳或当前时间
        event_timestamp = timestamp if timestamp else datetime.now().isoformat()

        # 创建事件记录
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "event_name": self.EVENT_TYPES.get(event_type, "未知事件"),
            "timestamp": event_timestamp,
            "title": title,
            "description": description,
            "related_id": related_id,
            "metadata": metadata or {},
        }

        # 添加到事件列表
        trajectory["events"].append(event)

        # 更新统计
        stats = trajectory["stats"]
        stats["total_events"] += 1
        stats["last_active"] = event_timestamp

        # 更新类型统计
        if event_type in ["publish_doc", "update_doc"]:
            stats["doc_count"] = stats.get("doc_count", 0) + 1
        elif event_type == "answer_question":
            stats["answer_count"] = stats.get("answer_count", 0) + 1
        elif event_type == "milestone":
            stats["milestone_count"] = stats.get("milestone_count", 0) + 1

        # 计算活跃天数
        self._update_active_days(stats)

        # 清理过期和超量事件
        self._cleanup_events(trajectory)

        # 保存
        self._save_member_trajectory(member_id, trajectory)

        logger.debug(
            f"[Trajectory] 记录事件: member={member_id}, "
            f"type={event_type}, title={title[:30]}"
        )
        return event_id

    def _update_active_days(self, stats: dict):
        """更新活跃天数统计"""
        # 简单实现：基于 last_active 计算
        # 未来可以更精确地统计
        pass

    def _cleanup_events(self, trajectory: dict):
        """清理过期和超量事件"""
        events = trajectory.get("events", [])
        if not events:
            return

        # 1. 清理过期事件
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        valid_events = []
        for event in events:
            timestamp = event.get("timestamp", "")
            if timestamp:
                try:
                    event_date = datetime.fromisoformat(timestamp)
                    if event_date > cutoff_date:
                        valid_events.append(event)
                except ValueError:
                    valid_events.append(event)  # 无效日期保留

        # 2. 限制事件数量
        if len(valid_events) > self.max_events:
            # 保留最近的 max_events 个
            valid_events = valid_events[-self.max_events:]

        trajectory["events"] = valid_events

    def get_trajectory(
        self,
        member_id: str,
        days: int = 30,
        event_types: Optional[List[str]] = None,
    ) -> List[dict]:
        """获取成员最近 N 天的活动轨迹

        Args:
            member_id: 成员标识
            days: 查询天数范围
            event_types: 筛选事件类型（可选）

        Returns:
            事件列表，按时间倒序
        """
        trajectory = self._load_member_trajectory(member_id)
        events = trajectory.get("events", [])

        cutoff_date = datetime.now() - timedelta(days=days)
        result = []

        for event in events:
            timestamp = event.get("timestamp", "")
            if timestamp:
                try:
                    event_date = datetime.fromisoformat(timestamp)
                    if event_date < cutoff_date:
                        continue
                except ValueError:
                    pass

            # 筛选事件类型
            if event_types and event.get("event_type") not in event_types:
                continue

            result.append(event)

        # 按时间倒序
        result.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return result

    def get_member_stats(self, member_id: str) -> dict:
        """获取成员统计信息"""
        trajectory = self._load_member_trajectory(member_id)
        return trajectory.get("stats", {})

    def get_all_active_members(self, days: int = 30) -> List[dict]:
        """获取所有活跃成员列表

        Args:
            days: 活跃判定天数

        Returns:
            活跃成员列表 [{member_id, stats, last_active}]
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        active_members = []

        # 遍历轨迹文件
        for file_path in self.trajectory_dir.glob("*.json"):
            member_id = file_path.stem
            trajectory = self._load_member_trajectory(member_id)

            stats = trajectory.get("stats", {})
            last_active = stats.get("last_active")

            if last_active:
                try:
                    last_date = datetime.fromisoformat(last_active)
                    if last_date > cutoff_date:
                        active_members.append({
                            "member_id": member_id,
                            "stats": stats,
                            "last_active": last_active,
                        })
                except ValueError:
                    pass

        # 按活跃时间排序
        active_members.sort(key=lambda x: x.get("last_active", ""), reverse=True)
        return active_members

    def search_by_topic(self, topic: str, days: int = 30) -> List[dict]:
        """根据主题搜索相关成员

        Args:
            topic: 搜索主题关键词
            days: 查询天数范围

        Returns:
            成员列表 [{member_id, matching_events}]
        """
        active_members = self.get_all_active_members(days)
        matching = []

        for member in active_members:
            member_id = member["member_id"]
            events = self.get_trajectory(member_id, days)

            # 匹配事件标题或描述包含关键词
            matching_events = []
            for event in events:
                title = event.get("title", "").lower()
                desc = event.get("description", "").lower()
                if topic.lower() in title or topic.lower() in desc:
                    matching_events.append(event)

            if matching_events:
                matching.append({
                    "member_id": member_id,
                    "stats": member["stats"],
                    "matching_events": matching_events,
                    "match_count": len(matching_events),
                })

        # 按匹配数排序
        matching.sort(key=lambda x: x.get("match_count", 0), reverse=True)
        return matching

    def clear_trajectory(self, member_id: str) -> bool:
        """清除成员轨迹

        Args:
            member_id: 成员标识

        Returns:
            是否成功
        """
        member_file = self._get_member_file(member_id)
        with self._lock:
            if member_file.exists():
                member_file.unlink()

        # 清除缓存
        with self._cache_lock:
            self._cache.pop(member_id, None)

        logger.info(f"[Trajectory] 清除成员轨迹: {member_id}")
        return True