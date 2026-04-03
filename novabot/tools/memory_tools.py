"""
NovaBot 记忆相关 Agent 工具
回忆对话历史、查看学习进度、问题档案
"""

from dataclasses import dataclass, field
from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger

from .base import BaseTool


@dataclass
class RecallConversationTool(BaseTool):
    """回忆对话历史工具

    当用户问"我上次问过什么"、"还记得那个问题吗"时调用。
    """

    name: str = "recall_conversation"
    description: str = (
        "回忆用户之前的对话历史。"
        "当用户问'我上次问过什么'、'还记得吗'、'之前聊过'、'上次的问题'时调用。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词，用于匹配历史对话（可选）"
            },
            "limit": {
                "type": "integer",
                "description": "返回数量限制，默认 5",
                "default": 5
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, keyword: str = "", limit: int = 5) -> str:
        """回忆对话历史

        Args:
            event: 消息事件
            keyword: 搜索关键词（可选）
            limit: 返回数量

        Returns:
            对话历史摘要
        """
        try:
            # 获取用户标识
            platform_id = event.get_sender_id()
            binding = self.plugin.storage.get_binding(platform_id)

            if not binding:
                return "用户未绑定语雀账号，无法回忆对话历史。"

            yuque_id = binding.get("yuque_id")
            if not yuque_id:
                return "绑定信息异常，无法回忆对话历史。"

            # 检查记忆管理器是否初始化
            if not self.plugin.memory_manager:
                return "长期记忆系统未初始化。"

            # 搜索或获取最近对话
            if keyword and keyword.strip():
                sessions = self.plugin.memory_manager.search_conversations(
                    str(yuque_id), keyword.strip(), limit=limit
                )
                if not sessions:
                    return f"未找到包含「{keyword}」的对话记录。"
            else:
                sessions = self.plugin.memory_manager.get_recent_sessions(
                    str(yuque_id), limit=limit
                )
                if not sessions:
                    return "暂无对话历史记录。"

            # 格式化输出
            lines = ["【对话回忆】"]
            for session in sessions:
                started_at = session.get("started_at", "")
                if started_at:
                    # 格式化日期
                    from datetime import datetime
                    try:
                        dt = datetime.fromisoformat(started_at)
                        date_str = dt.strftime("%m-%d %H:%M")
                    except ValueError:
                        date_str = started_at[:10]
                else:
                    date_str = "未知日期"

                summary = session.get("summary", "无摘要")
                lines.append(f"• {date_str}: {summary}")

            lines.append("\n提示：可以说「详细说说第X条」查看完整对话。")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[RecallTool] 回忆对话失败: {e}", exc_info=True)
            return f"回忆对话时出错: {e}"


@dataclass
class GetSessionDetailTool(BaseTool):
    """获取对话详情工具

    当用户想查看某个具体对话的完整内容时调用。
    """

    name: str = "get_session_detail"
    description: str = (
        "获取某个对话的完整详情。"
        "当用户说'详细说说第X条'、'展开第X条'、'完整对话'时调用。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "会话 ID（从 recall_conversation 结果中获取）"
            }
        },
        "required": ["session_id"]
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, session_id: str = "") -> str:
        """获取对话详情

        Args:
            event: 消息事件
            session_id: 会话 ID

        Returns:
            对话详情
        """
        try:
            if not session_id:
                return "请提供会话 ID，例如：详细说说第 abc123 条"

            # 获取用户标识
            platform_id = event.get_sender_id()
            binding = self.plugin.storage.get_binding(platform_id)

            if not binding:
                return "用户未绑定语雀账号，无法查看对话详情。"

            yuque_id = binding.get("yuque_id")
            if not yuque_id:
                return "绑定信息异常。"

            # 检查记忆管理器
            if not self.plugin.memory_manager:
                return "长期记忆系统未初始化。"

            # 获取详情
            detail = self.plugin.memory_manager.get_session_detail(str(yuque_id), session_id)
            if not detail:
                return f"未找到会话 {session_id}，可能已被清除。"

            # 格式化输出
            from datetime import datetime
            started_at = detail.get("started_at", "")
            if started_at:
                try:
                    dt = datetime.fromisoformat(started_at)
                    date_str = dt.strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    date_str = started_at[:19]
            else:
                date_str = "未知时间"

            lines = [
                f"【对话详情】 {session_id}",
                f"时间: {date_str}",
                f"摘要: {detail.get('summary', '无')}",
                "",
                "完整对话:",
                "━━━━━━━━━━━━━━━",
            ]

            messages = detail.get("messages", [])
            for msg in messages:
                role = "用户" if msg.get("role") == "user" else "NovaBot"
                content = msg.get("content", "")
                lines.append(f"{role}: {content}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[SessionDetailTool] 获取详情失败: {e}", exc_info=True)
            return f"获取对话详情时出错: {e}"


@dataclass
class GetUserStatsTool(BaseTool):
    """获取用户统计工具

    查看用户的对话统计信息。
    """

    name: str = "get_user_stats"
    description: str = (
        "获取用户的对话统计信息。"
        "当用户问'我聊过多少次'、'对话统计'、'活跃度'时调用。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent) -> str:
        """获取用户统计

        Args:
            event: 消息事件

        Returns:
            统计信息
        """
        try:
            platform_id = event.get_sender_id()
            binding = self.plugin.storage.get_binding(platform_id)

            if not binding:
                return "用户未绑定语雀账号。"

            yuque_id = binding.get("yuque_id")
            yuque_name = binding.get("yuque_name", "未知")

            if not yuque_id:
                return "绑定信息异常。"

            # 检查记忆管理器
            if not self.plugin.memory_manager:
                return "长期记忆系统未初始化。"

            stats = self.plugin.memory_manager.get_user_stats(str(yuque_id))

            lines = [
                f"📊 {yuque_name} 的对话统计",
                "━━━━━━━━━━━━━━━",
                f"• 总会话数: {stats.get('total_sessions', 0)}",
                f"• 总消息数: {stats.get('total_messages', 0)}",
                f"• 近7天活跃: {stats.get('recent_7_days', 0)} 次",
            ]

            first = stats.get("first_conversation")
            if first:
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(first)
                    first_str = dt.strftime("%Y-%m-%d")
                except ValueError:
                    first_str = first[:10]
                lines.append(f"• 首次对话: {first_str}")

            last = stats.get("last_conversation")
            if last:
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(last)
                    last_str = dt.strftime("%m-%d %H:%M")
                except ValueError:
                    last_str = last[:16]
                lines.append(f"• 最近对话: {last_str}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[StatsTool] 获取统计失败: {e}", exc_info=True)
            return f"获取统计时出错: {e}"


# 导出所有记忆工具
MEMORY_TOOLS = [
    RecallConversationTool,
    GetSessionDetailTool,
    GetUserStatsTool,
]


@dataclass
class GetLearningProgressTool(BaseTool):
    """获取学习进度工具

    当用户问"我学到哪了"、"学习进度"时调用。
    """

    name: str = "get_learning_progress"
    description: str = (
        "查看用户的学习进度。"
        "当用户问'我学到哪了'、'学习进度'、'爬虫学到什么程度'、'XX学得怎么样'时调用。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "学习领域（如'爬虫'），不传则返回所有领域"
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, domain: str = "") -> str:
        """获取学习进度

        Args:
            event: 消息事件
            domain: 学习领域（可选）

        Returns:
            学习进度信息
        """
        try:
            platform_id = event.get_sender_id()
            binding = self.plugin.storage.get_binding(platform_id)

            if not binding:
                return "用户未绑定语雀账号。"

            yuque_id = binding.get("yuque_id")
            yuque_name = binding.get("yuque_name", "未知")

            if not yuque_id:
                return "绑定信息异常。"

            # 检查记忆管理器
            if not self.plugin.memory_manager:
                return "长期记忆系统未初始化。"

            level_map = {
                "beginner": "入门",
                "intermediate": "进阶",
                "advanced": "高级",
            }

            if domain and domain.strip():
                # 返回指定领域
                progress = self.plugin.memory_manager.get_learning_progress(
                    str(yuque_id), domain.strip()
                )

                milestones = progress.get("milestones", [])
                level = progress.get("level", "beginner")
                next_step = progress.get("next_step")

                lines = [
                    f"📊 {yuque_name} 的「{domain.strip()}」学习进度",
                    "━━━━━━━━━━━━━━━",
                    f"等级: {level_map.get(level, level)}",
                ]

                if milestones:
                    lines.append(f"\n里程碑 ({len(milestones)} 个):")
                    for m in milestones[-10:]:  # 最近 10 个
                        date = m.get("date", "")
                        event_desc = m.get("event", "")
                        lines.append(f"• {date} - {event_desc}")
                else:
                    lines.append("\n暂无学习记录。")

                if next_step:
                    lines.append(f"\n下一步建议: {next_step}")

                return "\n".join(lines)

            else:
                # 返回所有领域
                progress = self.plugin.memory_manager.get_learning_progress(str(yuque_id))

                if not progress:
                    return f"{yuque_name} 暂无学习进度记录。\n\n使用 /progress add <领域> <事件> 添加学习里程碑。"

                lines = [
                    f"📊 {yuque_name} 的学习进度",
                    "━━━━━━━━━━━━━━━",
                ]

                for domain_name, data in progress.items():
                    level = data.get("level", "beginner")
                    milestones_count = len(data.get("milestones", []))
                    last_active = data.get("last_active", "无记录")

                    lines.append(
                        f"• {domain_name}: {level_map.get(level, level)} "
                        f"({milestones_count} 个里程碑, 最近: {last_active})"
                    )

                lines.append("\n使用 /progress <领域> 查看详情")
                return "\n".join(lines)

        except Exception as e:
            logger.error(f"[LearningProgressTool] 获取学习进度失败: {e}", exc_info=True)
            return f"获取学习进度时出错: {e}"


@dataclass
class RecordLearningMilestoneTool(BaseTool):
    """记录学习里程碑工具

    当用户说"我学完了XX"、"我掌握了XX"时调用。
    """

    name: str = "record_learning_milestone"
    description: str = (
        "记录用户的学习里程碑。"
        "当用户说'我学完了XX'、'我掌握了XX'、'我完成了XX教程'时调用。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "学习领域（如'爬虫'、'LLM应用开发'）"
            },
            "event": {
                "type": "string",
                "description": "里程碑事件描述（如'完成基础教程'、'掌握 Selenium'）"
            },
            "doc_title": {
                "type": "string",
                "description": "相关文档标题（可选）"
            }
        },
        "required": ["domain", "event"]
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        domain: str = "",
        event_desc: str = "",
        doc_title: str = "",
    ) -> str:
        """记录学习里程碑

        Args:
            event: 消息事件
            domain: 学习领域
            event_desc: 里程碑事件描述
            doc_title: 相关文档标题

        Returns:
            记录结果
        """
        try:
            if not domain or not event_desc:
                return "请提供学习领域和事件描述。"

            platform_id = event.get_sender_id()
            binding = self.plugin.storage.get_binding(platform_id)

            if not binding:
                return "用户未绑定语雀账号。"

            yuque_id = binding.get("yuque_id")
            yuque_name = binding.get("yuque_name", "未知")

            if not yuque_id:
                return "绑定信息异常。"

            # 检查记忆管理器
            if not self.plugin.memory_manager:
                return "长期记忆系统未初始化。"

            # 记录里程碑
            success = self.plugin.memory_manager.add_learning_milestone(
                user_id=str(yuque_id),
                domain=domain.strip(),
                event=event_desc.strip(),
                doc_title=doc_title.strip() if doc_title else None,
            )

            if success:
                return f"✅ 已记录 {yuque_name} 的学习里程碑：{domain} - {event_desc}"
            else:
                return "记录失败，请稍后重试。"

        except Exception as e:
            logger.error(f"[RecordMilestoneTool] 记录里程碑失败: {e}", exc_info=True)
            return f"记录里程碑时出错: {e}"