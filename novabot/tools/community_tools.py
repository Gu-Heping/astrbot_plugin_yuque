"""
NovaBot 社团层 Agent 工具
成员轨迹、协作网络
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger

from .base import BaseTool


@dataclass
class GetMemberTrajectoryTool(BaseTool):
    """获取成员轨迹工具

    当用户问"某人最近在做什么"、"谁在做某事"时调用。
    """

    name: str = "get_member_trajectory"
    description: str = (
        "查看某个成员最近在做什么。"
        "当用户问'黄亮最近在做什么'、'谁在做爬虫'、'某人的活动'时调用。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "member_name": {
                "type": "string",
                "description": "成员姓名或用户名"
            },
            "topic": {
                "type": "string",
                "description": "筛选主题关键词（可选）"
            },
            "days": {
                "type": "integer",
                "description": "查询天数范围，默认 30 天",
                "default": 30
            }
        },
        "required": ["member_name"]
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        member_name: str,
        topic: str = "",
        days: int = 30,
    ) -> str:
        """获取成员轨迹

        Args:
            event: 消息事件
            member_name: 成员姓名
            topic: 筛选主题
            days: 查询天数

        Returns:
            成员轨迹摘要
        """
        try:
            # 检查轨迹管理器
            if not hasattr(self.plugin, "trajectory_manager") or not self.plugin.trajectory_manager:
                return "成员轨迹系统未初始化。"

            # 尝试从成员缓存中查找成员 ID
            member_id = await self._resolve_member_id(member_name)
            if not member_id:
                # 如果是主题搜索，返回相关成员
                if topic:
                    return await self._search_by_topic(topic, days)
                return f"未找到成员「{member_name}」。请确认姓名是否正确。"

            # 获取轨迹
            event_types = None
            if topic:
                # 根据主题推断事件类型
                pass

            trajectory = self.plugin.trajectory_manager.get_trajectory(
                member_id, days=days, event_types=event_types
            )

            if not trajectory:
                return f"「{member_name}」最近 {days} 天暂无活动记录。"

            # 筛选主题
            if topic:
                filtered = []
                for evt in trajectory:
                    title = evt.get("title", "").lower()
                    desc = evt.get("description", "").lower()
                    if topic.lower() in title or topic.lower() in desc:
                        filtered.append(evt)
                if not filtered:
                    return f"「{member_name}」最近 {days} 天没有与「{topic}」相关的活动。"
                trajectory = filtered

            # 格式化输出
            lines = [f"【{member_name} 最近活动】"]
            for evt in trajectory[:10]:  # 最多显示 10 条
                timestamp = evt.get("timestamp", "")
                if timestamp:
                    from datetime import datetime
                    try:
                        dt = datetime.fromisoformat(timestamp)
                        date_str = dt.strftime("%m-%d")
                    except ValueError:
                        date_str = timestamp[:10]
                else:
                    date_str = "未知"

                event_name = evt.get("event_name", "活动")
                title = evt.get("title", "")
                lines.append(f"• {date_str} - {event_name}：{title[:30]}")

            stats = self.plugin.trajectory_manager.get_member_stats(member_id)
            lines.append(f"\n统计：共 {stats.get('total_events', 0)} 次活动")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[GetMemberTrajectory] 执行失败: {e}", exc_info=True)
            return f"获取成员轨迹时出错：{str(e)}"

    async def _resolve_member_id(self, member_name: str) -> Optional[str]:
        """解析成员姓名到成员 ID"""
        # 从团队成员缓存查找
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.load_members()
            for user_id, info in members.items():
                name = info.get("name", "")
                login = info.get("login", "")
                if member_name in [name, login, name.lower(), login.lower()]:
                    return str(user_id)

        # 从绑定记录查找
        # TODO: 实现从绑定记录查找

        return None

    async def _search_by_topic(self, topic: str, days: int) -> str:
        """根据主题搜索相关成员"""
        if not self.plugin.trajectory_manager:
            return "轨迹系统未初始化。"

        results = self.plugin.trajectory_manager.search_by_topic(topic, days)

        if not results:
            return f"最近 {days} 天没有成员在做「{topic}」相关的事情。"

        lines = [f"【与「{topic}」相关的成员活动】"]
        for result in results[:5]:  # 最多显示 5 个成员
            member_id = result.get("member_id", "")
            match_count = result.get("match_count", 0)
            events = result.get("matching_events", [])[:3]

            lines.append(f"\n{member_id}（{match_count} 次相关活动）")
            for evt in events:
                event_name = evt.get("event_name", "")
                title = evt.get("title", "")
                lines.append(f"  • {event_name}：{title[:30]}")

        return "\n".join(lines)


@dataclass
class FindCollaboratorsTool(BaseTool):
    """寻找协作伙伴工具

    当用户问"我想找人一起做某事"、"谁可以帮我"时调用。
    """

    name: str = "find_collaborators"
    description: str = (
        "根据主题推荐协作伙伴。"
        "当用户问'我想找人一起做项目'、'谁可以帮我'、'找学习伙伴'时调用。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "协作主题或领域（可选）"
            },
            "member_name": {
                "type": "string",
                "description": "指定成员，查找其潜在协作伙伴（可选）"
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        topic: str = "",
        member_name: str = "",
    ) -> str:
        """寻找协作伙伴

        Args:
            event: 消息事件
            topic: 协作主题
            member_name: 指定成员

        Returns:
            协作伙伴推荐
        """
        try:
            # 检查协作网络管理器
            if not hasattr(self.plugin, "collaboration_manager") or not self.plugin.collaboration_manager:
                return "协作网络系统未初始化。"

            # 如果指定了成员，查找其潜在协作伙伴
            if member_name:
                member_id = await self._resolve_member_id(member_name)
                if not member_id:
                    return f"未找到成员「{member_name}」。"

                potential = self.plugin.collaboration_manager.find_potential_collaborators(
                    member_id, topic=topic, exclude_existing=True
                )

                if not potential:
                    return f"暂无「{member_name}」的潜在协作伙伴推荐。"

                lines = [f"【{member_name} 的潜在协作伙伴】"]
                for p in potential[:5]:
                    partner_id = p.get("member_id", "")
                    score = p.get("match_score", 0)
                    reasons = p.get("match_reasons", [])

                    lines.append(f"\n{partner_id}（匹配度 {score:.0%}）")
                    for reason in reasons:
                        lines.append(f"  • {reason}")

                return "\n".join(lines)

            # 否则，根据主题搜索有相关经验的成员
            if topic:
                return await self._find_experts_by_topic(topic)

            # 默认：展示协作网络统计
            stats = self.plugin.collaboration_manager.get_network_stats()
            return (
                f"【协作网络统计】\n"
                f"• 总协作关系：{stats.get('total_collaborations', 0)} 条\n"
                f"• 参与成员：{stats.get('total_members', 0)} 人\n"
                f"• 平均协作强度：{stats.get('avg_strength', 0):.2f}\n\n"
                f"请告诉我具体想找什么主题的协作伙伴？"
            )

        except Exception as e:
            logger.error(f"[FindCollaborators] 执行失败: {e}", exc_info=True)
            return f"寻找协作伙伴时出错：{str(e)}"

    async def _resolve_member_id(self, member_name: str) -> Optional[str]:
        """解析成员姓名到成员 ID"""
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.get_all_members()
            for member in members:
                name = member.get("name", "")
                login = member.get("login", "")
                if member_name in [name, login, name.lower(), login.lower()]:
                    return str(member.get("user_id") or member.get("login"))
        return None

    async def _find_experts_by_topic(self, topic: str) -> str:
        """根据主题找有相关经验的成员"""
        # 从轨迹系统搜索
        if hasattr(self.plugin, "trajectory_manager") and self.plugin.trajectory_manager:
            results = self.plugin.trajectory_manager.search_by_topic(topic, days=60)

            if results:
                lines = [f"【「{topic}」领域活跃成员】"]
                for result in results[:5]:
                    member_id = result.get("member_id", "")
                    match_count = result.get("match_count", 0)
                    stats = result.get("stats", {})

                    lines.append(
                        f"\n{member_id}\n"
                        f"  • 相关活动 {match_count} 次\n"
                        f"  • 文档 {stats.get('doc_count', 0)} 篇"
                    )

                lines.append("\n建议直接联系他们，或者在群里讨论相关话题。")
                return "\n".join(lines)

        # 从文档索引搜索相关作者
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            doc_index = self.plugin.storage.doc_index
            if doc_index:
                # 搜索相关文档并提取作者
                docs = doc_index.search_docs(keyword=topic, limit=20)
                author_count: dict = {}
                for doc in docs:
                    author = doc.get("author") or doc.get("creator_id")
                    if author:
                        author_count[str(author)] = author_count.get(str(author), 0) + 1

                if author_count:
                    sorted_authors = sorted(
                        author_count.items(),
                        key=lambda x: x[1],
                        reverse=True
                    )[:5]

                    lines = [f"【写过「{topic}」相关文档的成员】"]
                    for author, count in sorted_authors:
                        lines.append(f"• {author}（{count} 篇文档）")

                    lines.append("\n可以查看他们的文档学习，或者直接请教问题。")
                    return "\n".join(lines)

        return f"暂未找到「{topic}」相关领域的活跃成员。建议在群里提问。"


@dataclass
class GetCollaboratorsTool(BaseTool):
    """获取协作伙伴工具

    查看某成员已有的协作关系。
    """

    name: str = "get_collaborators"
    description: str = (
        "查看某个成员的协作伙伴。"
        "当用户问'某人跟谁协作过'、'某人的伙伴'时调用。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "member_name": {
                "type": "string",
                "description": "成员姓名或用户名"
            },
            "min_strength": {
                "type": "number",
                "description": "最小协作强度过滤（0-1），默认 0",
                "default": 0
            }
        },
        "required": ["member_name"]
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        member_name: str,
        min_strength: float = 0,
    ) -> str:
        """获取协作伙伴

        Args:
            event: 消息事件
            member_name: 成员姓名
            min_strength: 最小强度

        Returns:
            协作伙伴列表
        """
        try:
            if not hasattr(self.plugin, "collaboration_manager") or not self.plugin.collaboration_manager:
                return "协作网络系统未初始化。"

            member_id = await self._resolve_member_id(member_name)
            if not member_id:
                return f"未找到成员「{member_name}」。"

            collaborators = self.plugin.collaboration_manager.get_collaborators(
                member_id, min_strength=min_strength
            )

            if not collaborators:
                return f"「{member_name}」暂无协作记录。"

            lines = [f"【{member_name} 的协作伙伴】"]
            for collab in collaborators[:10]:
                partner_id = collab.get("member_id", "")
                strength = collab.get("strength", 0)
                source_name = collab.get("source_name", "")
                context = collab.get("context", "")

                line = f"• {partner_id}（强度 {strength:.0%}，{source_name}"
                if context:
                    line += f"：{context}"
                line += "）"
                lines.append(line)

            stats = self.plugin.collaboration_manager.get_member_stats(member_id)
            lines.append(f"\n统计：{stats.get('collaborator_count', 0)} 位协作伙伴")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[GetCollaborators] 执行失败: {e}", exc_info=True)
            return f"获取协作伙伴时出错：{str(e)}"

    async def _resolve_member_id(self, member_name: str) -> Optional[str]:
        """解析成员姓名到成员 ID"""
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.get_all_members()
            for member in members:
                name = member.get("name", "")
                login = member.get("login", "")
                if member_name in [name, login, name.lower(), login.lower()]:
                    return str(member.get("user_id") or member.get("login"))
        return None