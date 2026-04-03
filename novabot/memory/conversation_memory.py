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
                    else:
                        # 迁移：补全缺失的字段（兼容旧版本）
                        memory = self._migrate_memory(memory)
                except json.JSONDecodeError as e:
                    logger.warning(f"[Memory] 用户记忆文件损坏 ({user_id}): {e}")
                    memory = self._create_empty_memory(user_id)
            else:
                memory = self._create_empty_memory(user_id)

            # 存入缓存
            with self._cache_lock:
                self._cache[user_id] = memory

            # 如果发生了迁移，保存一次（在锁外调用 _save_user_memory，它有自己的锁）
            migrated = memory.pop("_migrated", False)

        # 锁外保存（_save_user_memory 有自己的锁）
        if migrated:
            self._save_user_memory(user_id, memory)
            logger.info(f"[Memory] 数据迁移完成: {user_id}")

        return memory

    def _migrate_memory(self, memory: dict) -> dict:
        """迁移旧版本数据结构

        确保所有必要字段存在，兼容旧版本升级。

        Args:
            memory: 加载的记忆字典

        Returns:
            迁移后的记忆字典
        """
        changed = False

        # v0.26.0 基础字段
        if "sessions" not in memory:
            memory["sessions"] = []
            changed = True
        if "stats" not in memory:
            memory["stats"] = {
                "total_sessions": 0,
                "total_messages": 0,
                "first_conversation": None,
                "last_conversation": None,
            }
            changed = True

        # v0.26.1 学习进度
        if "learning_progress" not in memory:
            memory["learning_progress"] = {}
            changed = True

        # v0.26.2 问题档案
        if "question_archive" not in memory:
            memory["question_archive"] = []
            changed = True

        # 标记是否需要保存
        memory["_migrated"] = changed
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
            "learning_progress": {},  # 学习进度追踪
            "question_archive": [],   # 问题档案
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

    # ========== 学习进度追踪 ==========

    def add_learning_milestone(
        self,
        user_id: str,
        domain: str,
        event: str,
        doc_title: str = None,
        doc_id: int = None,
    ) -> bool:
        """添加学习里程碑

        Args:
            user_id: 用户标识
            domain: 学习领域（如"爬虫"）
            event: 里程碑事件描述
            doc_title: 相关文档标题
            doc_id: 相关文档 ID

        Returns:
            是否添加成功
        """
        if not user_id or not domain or not event:
            logger.warning("[Memory] 无效参数，跳过学习里程碑记录")
            return False

        # 加载记忆
        memory = self._load_user_memory(user_id)

        # 确保 learning_progress 存在
        if "learning_progress" not in memory:
            memory["learning_progress"] = {}

        # 规范化领域名（去除空格、统一大小写）
        domain_key = domain.strip()

        # 确保领域存在
        if domain_key not in memory["learning_progress"]:
            memory["learning_progress"][domain_key] = {
                "level": "beginner",
                "milestones": [],
                "last_active": None,
                "next_step": None,
                "related_docs": [],
            }

        progress = memory["learning_progress"][domain_key]

        # 创建里程碑
        milestone = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "timestamp": datetime.now().isoformat(),
            "event": event.strip(),
        }
        if doc_title:
            milestone["doc_title"] = doc_title
        if doc_id:
            milestone["doc_id"] = doc_id

        # 添加里程碑
        progress["milestones"].append(milestone)
        progress["last_active"] = datetime.now().strftime("%Y-%m-%d")

        # 记录相关文档
        if doc_id and doc_id not in progress["related_docs"]:
            progress["related_docs"].append(doc_id)

        # 限制里程碑数量（最多 50 个）
        if len(progress["milestones"]) > 50:
            progress["milestones"] = progress["milestones"][-50:]

        # 保存
        self._save_user_memory(user_id, memory)

        logger.info(f"[Memory] 添加学习里程碑: user={user_id}, domain={domain_key}, event={event}")
        return True

    def get_learning_progress(self, user_id: str, domain: str = None) -> dict:
        """获取学习进度

        Args:
            user_id: 用户标识
            domain: 学习领域（可选，不传则返回所有领域）

        Returns:
            学习进度字典
        """
        memory = self._load_user_memory(user_id)
        progress = memory.get("learning_progress", {})

        if domain:
            # 返回指定领域
            domain_key = domain.strip()
            return progress.get(domain_key, {
                "level": "beginner",
                "milestones": [],
                "last_active": None,
                "next_step": None,
                "related_docs": [],
            })

        # 返回所有领域
        return progress

    def update_learning_level(self, user_id: str, domain: str, level: str) -> bool:
        """更新学习等级

        Args:
            user_id: 用户标识
            domain: 学习领域
            level: beginner/intermediate/advanced

        Returns:
            是否更新成功
        """
        if level not in ("beginner", "intermediate", "advanced"):
            logger.warning(f"[Memory] 无效的学习等级: {level}")
            return False

        memory = self._load_user_memory(user_id)

        if "learning_progress" not in memory:
            memory["learning_progress"] = {}

        domain_key = domain.strip()

        if domain_key not in memory["learning_progress"]:
            memory["learning_progress"][domain_key] = {
                "level": level,
                "milestones": [],
                "last_active": None,
                "next_step": None,
                "related_docs": [],
            }
        else:
            memory["learning_progress"][domain_key]["level"] = level

        self._save_user_memory(user_id, memory)
        logger.info(f"[Memory] 更新学习等级: user={user_id}, domain={domain_key}, level={level}")
        return True

    def set_next_step(self, user_id: str, domain: str, next_step: str) -> bool:
        """设置下一步学习建议

        Args:
            user_id: 用户标识
            domain: 学习领域
            next_step: 下一步建议

        Returns:
            是否设置成功
        """
        memory = self._load_user_memory(user_id)

        if "learning_progress" not in memory:
            memory["learning_progress"] = {}

        domain_key = domain.strip()

        if domain_key not in memory["learning_progress"]:
            memory["learning_progress"][domain_key] = {
                "level": "beginner",
                "milestones": [],
                "last_active": None,
                "next_step": next_step,
                "related_docs": [],
            }
        else:
            memory["learning_progress"][domain_key]["next_step"] = next_step

        self._save_user_memory(user_id, memory)
        return True

    def get_all_domains(self, user_id: str) -> List[str]:
        """获取用户所有学习领域

        Args:
            user_id: 用户标识

        Returns:
            学习领域列表
        """
        memory = self._load_user_memory(user_id)
        progress = memory.get("learning_progress", {})
        return list(progress.keys())

    def get_learning_summary(self, user_id: str) -> dict:
        """获取学习进度摘要

        Args:
            user_id: 用户标识

        Returns:
            摘要信息：各领域等级和里程碑数
        """
        progress = self.get_learning_progress(user_id)

        summary = {}
        for domain, data in progress.items():
            summary[domain] = {
                "level": data.get("level", "beginner"),
                "milestones_count": len(data.get("milestones", [])),
                "last_active": data.get("last_active"),
                "next_step": data.get("next_step"),
            }

        return summary

    # ========== 问题档案 ==========

    def add_question(
        self,
        user_id: str,
        question: str,
        session_id: str = None,
        related_docs: List[int] = None,
    ) -> str:
        """添加问题或更新已有问题的计数

        Args:
            user_id: 用户标识
            question: 问题内容
            session_id: 会话 ID
            related_docs: 相关文档 ID 列表

        Returns:
            question_id
        """
        if not user_id or not question:
            logger.warning("[Memory] 无效参数，跳过问题记录")
            return ""

        memory = self._load_user_memory(user_id)

        # 确保 question_archive 存在
        if "question_archive" not in memory:
            memory["question_archive"] = []

        question_text = question.strip()
        timestamp = datetime.now().strftime("%Y-%m-%d")

        # 尝试匹配相似问题
        similar_question = self._find_similar_question(
            memory["question_archive"], question_text
        )

        if similar_question:
            # 更新已有问题
            similar_question["ask_count"] += 1
            similar_question["last_asked"] = timestamp
            if session_id and session_id not in similar_question.get("asked_in_sessions", []):
                similar_question.setdefault("asked_in_sessions", []).append(session_id)
            if related_docs:
                for doc_id in related_docs:
                    if doc_id not in similar_question.get("related_docs", []):
                        similar_question.setdefault("related_docs", []).append(doc_id)

            self._save_user_memory(user_id, memory)
            logger.info(f"[Memory] 更新问题计数: {similar_question['question_id']}")
            return similar_question["question_id"]

        # 创建新问题
        question_id = f"q_{str(uuid4())[:8]}"
        new_question = {
            "question_id": question_id,
            "question": question_text,
            "first_asked": timestamp,
            "last_asked": timestamp,
            "ask_count": 1,
            "resolved": False,
            "resolution": None,
            "related_docs": related_docs or [],
            "suggested_mentor": None,
            "asked_in_sessions": [session_id] if session_id else [],
        }

        memory["question_archive"].append(new_question)

        # 限制问题数量（最多 100 个）
        if len(memory["question_archive"]) > 100:
            # 优先删除已解决的问题
            unresolved = [q for q in memory["question_archive"] if not q.get("resolved")]
            resolved = [q for q in memory["question_archive"] if q.get("resolved")]
            # 保留未解决的，删除最旧的已解决
            memory["question_archive"] = unresolved + resolved[-(100 - len(unresolved)):]

        self._save_user_memory(user_id, memory)
        logger.info(f"[Memory] 添加新问题: {question_id}")
        return question_id

    def _find_similar_question(self, questions: List[dict], question_text: str) -> Optional[dict]:
        """查找相似问题

        Args:
            questions: 问题列表
            question_text: 待匹配问题

        Returns:
            相似问题，未找到返回 None
        """
        if not questions:
            return None

        # 简单实现：关键词匹配
        # 提取关键词（去除常见词）
        stop_words = {"怎么", "如何", "什么", "为什么", "吗", "呢", "啊", "呀", "的", "是", "有"}
        keywords = set(question_text.lower().split())
        keywords = keywords - stop_words

        best_match = None
        best_score = 0

        for q in questions:
            q_text = q.get("question", "").lower()
            q_words = set(q_text.split()) - stop_words

            # 计算交集
            common = keywords & q_words
            if common:
                score = len(common) / max(len(keywords), len(q_words), 1)
                if score > 0.5 and score > best_score:
                    best_score = score
                    best_match = q

        return best_match

    def resolve_question(self, user_id: str, question_id: str, resolution: str = "") -> bool:
        """标记问题已解决

        Args:
            user_id: 用户标识
            question_id: 问题 ID
            resolution: 解决方案描述

        Returns:
            是否成功
        """
        memory = self._load_user_memory(user_id)
        questions = memory.get("question_archive", [])

        for q in questions:
            if q.get("question_id") == question_id:
                q["resolved"] = True
                q["resolution"] = resolution
                self._save_user_memory(user_id, memory)
                logger.info(f"[Memory] 问题已解决: {question_id}")
                return True

        return False

    def get_unresolved_questions(self, user_id: str) -> List[dict]:
        """获取未解决的问题

        Args:
            user_id: 用户标识

        Returns:
            未解决问题列表
        """
        memory = self._load_user_memory(user_id)
        questions = memory.get("question_archive", [])

        return [q for q in questions if not q.get("resolved")]

    def get_frequent_questions(
        self, user_id: str, min_ask_count: int = 2
    ) -> List[dict]:
        """获取反复出现的问题

        Args:
            user_id: 用户标识
            min_ask_count: 最小询问次数

        Returns:
            反复出现的问题列表
        """
        memory = self._load_user_memory(user_id)
        questions = memory.get("question_archive", [])

        return [q for q in questions if q.get("ask_count", 1) >= min_ask_count]

    def get_all_questions(self, user_id: str) -> List[dict]:
        """获取所有问题

        Args:
            user_id: 用户标识

        Returns:
            所有问题列表
        """
        memory = self._load_user_memory(user_id)
        return memory.get("question_archive", [])

    def check_question_history(self, user_id: str, question: str) -> Optional[dict]:
        """检查问题历史

        Args:
            user_id: 用户标识
            question: 问题内容

        Returns:
            历史问题记录，未找到返回 None
        """
        memory = self._load_user_memory(user_id)
        questions = memory.get("question_archive", [])

        return self._find_similar_question(questions, question)

    def set_suggested_mentor(
        self, user_id: str, question_id: str, mentor_name: str
    ) -> bool:
        """设置推荐导师

        Args:
            user_id: 用户标识
            question_id: 问题 ID
            mentor_name: 导师名称

        Returns:
            是否成功
        """
        memory = self._load_user_memory(user_id)
        questions = memory.get("question_archive", [])

        for q in questions:
            if q.get("question_id") == question_id:
                q["suggested_mentor"] = mentor_name
                self._save_user_memory(user_id, memory)
                return True

        return False

    def get_question_stats(self, user_id: str) -> dict:
        """获取问题统计

        Args:
            user_id: 用户标识

        Returns:
            统计信息
        """
        questions = self.get_all_questions(user_id)

        total = len(questions)
        resolved = sum(1 for q in questions if q.get("resolved"))
        unresolved = total - resolved
        frequent = sum(1 for q in questions if q.get("ask_count", 1) >= 2)

        return {
            "total": total,
            "resolved": resolved,
            "unresolved": unresolved,
            "frequent": frequent,
        }