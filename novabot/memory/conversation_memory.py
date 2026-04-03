"""
对话历史管理器
存储用户对话历史，支持搜索、回顾、清除
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from uuid import uuid4

from astrbot.api import logger


class ConversationMemory:
    """对话历史管理器"""

    # 默认配置
    DEFAULT_MAX_SESSIONS = 100  # 每用户最多保留的会话数
    DEFAULT_RETENTION_DAYS = 30  # 对话历史保留天数
    DEFAULT_MAX_MESSAGES_PER_SESSION = 50  # 每会话最多保留的消息数

    def __init__(
        self,
        data_dir: Path,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        """初始化对话历史管理器

        Args:
            data_dir: 数据存储目录
            max_sessions: 每用户最多保留的会话数
            retention_days: 对话历史保留天数
        """
        self.memory_dir = data_dir / "conversation_history"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        self.max_sessions = max_sessions
        self.retention_days = retention_days

        # 并发锁（保护文件写入）
        self._lock = threading.Lock()

        # 用户缓存（懒加载）
        self._cache: Dict[str, dict] = {}
        self._cache_lock = threading.Lock()

        logger.info(
            f"[ConversationMemory] 初始化完成: "
            f"max_sessions={max_sessions}, retention_days={retention_days}"
        )

    def _get_user_file(self, user_id: str) -> Path:
        """获取用户记忆文件路径

        Args:
            user_id: 用户标识（语雀 ID 或平台 ID）

        Returns:
            用户记忆文件路径
        """
        # 使用安全的文件名（避免特殊字符）
        safe_id = str(user_id).replace("/", "_").replace("\\", "_")
        return self.memory_dir / f"{safe_id}.json"

    def _load_user_memory(self, user_id: str) -> dict:
        """加载用户记忆（带缓存）

        Args:
            user_id: 用户标识

        Returns:
            用户记忆字典
        """
        # 检查缓存
        with self._cache_lock:
            if user_id in self._cache:
                return self._cache[user_id]

        # 从文件加载
        user_file = self._get_user_file(user_id)
        with self._lock:
            if user_file.exists():
                try:
                    memory = json.loads(user_file.read_text(encoding="utf-8"))
                    # 验证数据结构
                    if not isinstance(memory, dict):
                        memory = self._create_empty_memory(user_id)
                except json.JSONDecodeError as e:
                    logger.warning(f"[Memory] 用户记忆文件损坏 ({user_id}): {e}")
                    memory = self._create_empty_memory(user_id)
            else:
                memory = self._create_empty_memory(user_id)

            # 存入缓存
            with self._cache_lock:
                self._cache[user_id] = memory

            return memory

    def _save_user_memory(self, user_id: str, memory: dict):
        """保存用户记忆

        Args:
            user_id: 用户标识
            memory: 用户记忆字典
        """
        user_file = self._get_user_file(user_id)
        with self._lock:
            user_file.write_text(
                json.dumps(memory, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        # 更新缓存
        with self._cache_lock:
            self._cache[user_id] = memory

    def _create_empty_memory(self, user_id: str) -> dict:
        """创建空的用户记忆结构

        Args:
            user_id: 用户标识

        Returns:
            空的用户记忆字典
        """
        return {
            "user_id": user_id,
            "sessions": [],
            "stats": {
                "total_sessions": 0,
                "total_messages": 0,
                "first_conversation": None,
                "last_conversation": None,
            },
        }

    def add_session(
        self,
        user_id: str,
        umo: str,
        user_msg: str,
        assistant_msg: str,
        topics: Optional[List[str]] = None,
    ) -> str:
        """添加一次对话会话

        Args:
            user_id: 用户标识（语雀 ID）
            umo: 会话标识（平台来源）
            user_msg: 用户消息
            assistant_msg:助手回复
            topics: 对话主题标签（可选）

        Returns:
            会话 ID
        """
        if not user_id or not user_msg:
            logger.warning("[Memory] 无效参数，跳过记录")
            return ""

        # 加载记忆
        memory = self._load_user_memory(user_id)

        # 生成会话 ID
        session_id = str(uuid4())[:8]
        timestamp = datetime.now().isoformat()

        # 创建会话记录
        session = {
            "session_id": session_id,
            "umo": umo,
            "started_at": timestamp,
            "ended_at": timestamp,
            "message_count": 2,  # 用户 +助手
            "summary": self._generate_summary(user_msg, assistant_msg),
            "topics": topics or [],
            "messages": [
                {"role": "user", "content": user_msg, "timestamp": timestamp},
                {"role": "assistant", "content": assistant_msg, "timestamp": timestamp},
            ],
        }

        # 添加到会话列表
        memory["sessions"].append(session)

        # 更新统计
        stats = memory["stats"]
        stats["total_sessions"] += 1
        stats["total_messages"] += 2
        if stats["first_conversation"] is None:
            stats["first_conversation"] = timestamp
        stats["last_conversation"] = timestamp

        # 清理过期和超量会话
        self._cleanup_sessions(memory)

        # 保存
        self._save_user_memory(user_id, memory)

        logger.debug(f"[Memory] 记录对话: user={user_id}, session={session_id}")
        return session_id

    def _generate_summary(self, user_msg: str, assistant_msg: str) -> str:
        """生成对话摘要（简单版本：截取用户消息前50字）

        Args:
            user_msg: 用户消息
            assistant_msg:助手回复

        Returns:
            对话摘要
        """
        # 简单实现：截取用户消息作为摘要
        # 未来可以使用 LLM 生成更好的摘要
        summary = user_msg.strip()
        if len(summary) > 50:
            summary = summary[:50] + "..."
        return summary

    def _cleanup_sessions(self, memory: dict):
        """清理过期和超量会话

        Args:
            memory: 用户记忆字典
        """
        sessions = memory.get("sessions", [])
        if not sessions:
            return

        # 1. 清理过期会话
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        valid_sessions = []
        for session in sessions:
            started_at = session.get("started_at", "")
            if started_at:
                try:
                    session_date = datetime.fromisoformat(started_at)
                    if session_date >= cutoff_date:
                        valid_sessions.append(session)
                except ValueError:
                    # 无法解析日期，保留
                    valid_sessions.append(session)
            else:
                valid_sessions.append(session)

        # 2. 限制会话数量
        if len(valid_sessions) > self.max_sessions:
            # 按时间排序，保留最近的
            valid_sessions.sort(
                key=lambda s: s.get("started_at", ""),
                reverse=True
            )
            valid_sessions = valid_sessions[:self.max_sessions]

        memory["sessions"] = valid_sessions

    def get_recent_sessions(
        self,
        user_id: str,
        limit: int = 10,
    ) -> List[dict]:
        """获取最近的对话会话

        Args:
            user_id: 用户标识
            limit: 返回数量限制

        Returns:
            最近会话列表（不含完整消息，仅摘要）
        """
        memory = self._load_user_memory(user_id)
        sessions = memory.get("sessions", [])

        # 按时间降序
        sessions.sort(key=lambda s: s.get("started_at", ""), reverse=True)

        # 返回摘要（不含完整消息内容，节省内存）
        result = []
        for session in sessions[:limit]:
            result.append({
                "session_id": session.get("session_id"),
                "umo": session.get("umo"),
                "started_at": session.get("started_at"),
                "summary": session.get("summary"),
                "topics": session.get("topics", []),
                "message_count": session.get("message_count", 0),
            })

        return result

    def get_session_detail(self, user_id: str, session_id: str) -> Optional[dict]:
        """获取会话详情（含完整消息）

        Args:
            user_id: 用户标识
            session_id: 会话 ID

        Returns:
            会话详情，未找到返回 None
        """
        memory = self._load_user_memory(user_id)
        sessions = memory.get("sessions", [])

        for session in sessions:
            if session.get("session_id") == session_id:
                return session

        return None

    def search_conversations(
        self,
        user_id: str,
        keyword: str,
        limit: int = 20,
    ) -> List[dict]:
        """搜索对话内容

        Args:
            user_id: 用户标识
            keyword: 搜索关键词
            limit: 返回数量限制

        Returns:
            匹配的会话列表
        """
        if not keyword or not keyword.strip():
            return []

        keyword_lower = keyword.strip().lower()
        memory = self._load_user_memory(user_id)
        sessions = memory.get("sessions", [])

        result = []
        for session in sessions:
            # 搜索摘要
            summary = session.get("summary", "").lower()
            if keyword_lower in summary:
                result.append({
                    "session_id": session.get("session_id"),
                    "started_at": session.get("started_at"),
                    "summary": session.get("summary"),
                    "matched_in": "summary",
                })
                continue

            # 搜索消息内容
            messages = session.get("messages", [])
            for msg in messages:
                content = msg.get("content", "").lower()
                if keyword_lower in content:
                    result.append({
                        "session_id": session.get("session_id"),
                        "started_at": session.get("started_at"),
                        "summary": session.get("summary"),
                        "matched_in": "message",
                        "matched_role": msg.get("role"),
                        "matched_content_preview": content[:100],
                    })
                    break  # 一个会话只匹配一次

        # 按时间降序
        result.sort(key=lambda s: s.get("started_at", ""), reverse=True)
        return result[:limit]

    def get_user_stats(self, user_id: str) -> dict:
        """获取用户对话统计

        Args:
            user_id: 用户标识

        Returns:
            统计信息字典
        """
        memory = self._load_user_memory(user_id)
        stats = memory.get("stats", {})

        # 补充额外统计
        sessions = memory.get("sessions", [])
        recent_7_days = 0
        cutoff = datetime.now() - timedelta(days=7)

        for session in sessions:
            started_at = session.get("started_at", "")
            if started_at:
                try:
                    session_date = datetime.fromisoformat(started_at)
                    if session_date >= cutoff:
                        recent_7_days += 1
                except ValueError:
                    pass

        stats["recent_7_days"] = recent_7_days
        stats["sessions_count"] = len(sessions)

        return stats

    def clear_user_memory(self, user_id: str) -> bool:
        """清除用户记忆

        Args:
            user_id: 用户标识

        Returns:
            是否成功清除
        """
        user_file = self._get_user_file(user_id)

        with self._lock:
            if user_file.exists():
                try:
                    user_file.unlink()
                    logger.info(f"[Memory] 已清除用户记忆: {user_id}")
                except Exception as e:
                    logger.error(f"[Memory] 清除失败: {e}")
                    return False

        # 清除缓存
        with self._cache_lock:
            if user_id in self._cache:
                del self._cache[user_id]

        return True

    def recall_for_context(
        self,
        user_id: str,
        query: str,
        max_sessions: int = 3,
    ) -> str:
        """为对话提供上下文回忆

        检索与当前查询相关的历史对话，生成上下文文本。

        Args:
            user_id: 用户标识
            query: 当前查询
            max_sessions: 最大返回会话数

        Returns:
            上下文回忆文本
        """
        # 搜索相关对话
        related = self.search_conversations(user_id, query, limit=max_sessions)

        if not related:
            # 如果没有直接相关，返回最近的对话
            recent = self.get_recent_sessions(user_id, limit=max_sessions)
            if not recent:
                return ""
            related = recent

        # 构建上下文文本
        lines = []
        for session in related:
            started_at = session.get("started_at", "")
            if started_at:
                # 格式化日期
                try:
                    dt = datetime.fromisoformat(started_at)
                    date_str = dt.strftime("%m-%d %H:%M")
                except ValueError:
                    date_str = started_at[:10]

            summary = session.get("summary", "")
            lines.append(f"• {date_str}: {summary}")

        if lines:
            return "【历史对话】\n" + "\n".join(lines)
        return ""

    def get_last_topic(self, user_id: str) -> Optional[str]:
        """获取用户上次对话的主题

        Args:
            user_id: 用户标识

        Returns:
            上次对话的摘要/主题，无历史返回 None
        """
        memory = self._load_user_memory(user_id)
        sessions = memory.get("sessions", [])

        if not sessions:
            return None

        # 按时间降序，取最近一次
        sessions.sort(key=lambda s: s.get("started_at", ""), reverse=True)
        last_session = sessions[0]

        # 返回摘要或第一个主题
        summary = last_session.get("summary", "")
        topics = last_session.get("topics", [])

        if topics:
            return topics[0]
        return summary if summary else None