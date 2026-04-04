"""
NovaBot 社团层 Agent 工具
成员轨迹、协作网络
"""

from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict

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
        """获取成员轨迹"""
        try:
            if not hasattr(self.plugin, "trajectory_manager") or not self.plugin.trajectory_manager:
                return "成员轨迹系统未初始化。"

            member_id = await self._resolve_member_id(member_name)
            if not member_id:
                if topic:
                    return await self._search_by_topic(topic, days)
                return f"未找到成员「{member_name}」。请确认姓名是否正确。"

            trajectory = self.plugin.trajectory_manager.get_trajectory(
                member_id, days=days
            )

            if not trajectory:
                return f"「{member_name}」最近 {days} 天暂无活动记录。"

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

            lines = [f"【{member_name} 最近活动】"]
            for evt in trajectory[:10]:
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
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.load_members()
            for user_id, info in members.items():
                name = info.get("name", "")
                login = info.get("login", "")
                if member_name in [name, login, name.lower(), login.lower()]:
                    return str(user_id)
        return None

    async def _search_by_topic(self, topic: str, days: int) -> str:
        if not self.plugin.trajectory_manager:
            return "轨迹系统未初始化。"

        results = self.plugin.trajectory_manager.search_by_topic(topic, days)

        if not results:
            return f"最近 {days} 天没有成员在做「{topic}」相关的事情。"

        lines = [f"【与「{topic}」相关的成员活动】"]
        for result in results[:5]:
            member_id = result.get("member_id", "")
            member_name = self._resolve_member_name(member_id)
            match_count = result.get("match_count", 0)
            events = result.get("matching_events", [])[:3]

            lines.append(f"\n{member_name}（{match_count} 次相关活动）")
            for evt in events:
                event_name = evt.get("event_name", "")
                title = evt.get("title", "")
                lines.append(f"  • {event_name}：{title[:30]}")

        return "\n".join(lines)

    def _resolve_member_name(self, member_id: str) -> str:
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.load_members()
            info = members.get(member_id) or members.get(int(member_id) if member_id.isdigit() else None)
            if info:
                return info.get("name") or info.get("login") or member_id
        return member_id


@dataclass
class FindCollaboratorsTool(BaseTool):
    """寻找协作伙伴工具"""

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
        try:
            if not hasattr(self.plugin, "collaboration_manager") or not self.plugin.collaboration_manager:
                return "协作网络系统未初始化。"

            if member_name:
                member_id = await self._resolve_member_id(member_name)
                if not member_id:
                    return f"未找到成员「{member_name}」。"

                trajectory_mgr = getattr(self.plugin, "trajectory_manager", None)
                doc_idx = self.plugin._get_doc_index() if hasattr(self.plugin, "_get_doc_index") else None

                potential = self.plugin.collaboration_manager.find_potential_collaborators(
                    member_id,
                    topic=topic,
                    exclude_existing=True,
                    trajectory_manager=trajectory_mgr,
                    doc_index=doc_idx,
                )

                if not potential:
                    return f"暂无「{member_name}」的潜在协作伙伴推荐。"

                lines = [f"【{member_name} 的潜在协作伙伴】"]
                for p in potential[:5]:
                    partner_id = p.get("member_id", "")
                    partner_name = self._resolve_member_name(partner_id)
                    score = p.get("match_score", 0)
                    reasons = p.get("match_reasons", [])

                    lines.append(f"\n{partner_name}（匹配度 {score:.0%}）")
                    for reason in reasons:
                        lines.append(f"  • {reason}")

                return "\n".join(lines)

            if topic:
                return await self._find_experts_by_topic(topic)

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
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.load_members()
            for user_id, info in members.items():
                name = info.get("name", "")
                login = info.get("login", "")
                if member_name in [name, login, name.lower(), login.lower()]:
                    return str(user_id)
        return None

    def _resolve_member_name(self, member_id: str) -> str:
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.load_members()
            info = members.get(member_id) or members.get(int(member_id) if member_id.isdigit() else None)
            if info:
                return info.get("name") or info.get("login") or member_id
        return member_id

    async def _find_experts_by_topic(self, topic: str) -> str:
        if hasattr(self.plugin, "trajectory_manager") and self.plugin.trajectory_manager:
            results = self.plugin.trajectory_manager.search_by_topic(topic, days=60)

            if results:
                lines = [f"【「{topic}」领域活跃成员】"]
                for result in results[:5]:
                    member_id = result.get("member_id", "")
                    member_name = self._resolve_member_name(member_id)
                    match_count = result.get("match_count", 0)
                    stats = result.get("stats", {})

                    lines.append(
                        f"\n{member_name}\n"
                        f"  • 相关活动 {match_count} 次\n"
                        f"  • 文档 {stats.get('doc_count', 0)} 篇"
                    )

                lines.append("\n建议直接联系他们，或者在群里讨论相关话题。")
                return "\n".join(lines)

        if hasattr(self.plugin, "_get_doc_index"):
            doc_index = self.plugin._get_doc_index()
            if doc_index:
                docs = doc_index.search(title=topic, limit=20)
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
                        author_name = self._resolve_member_name(author)
                        lines.append(f"• {author_name}（{count} 篇文档）")

                    lines.append("\n可以查看他们的文档学习，或者直接请教问题。")
                    return "\n".join(lines)

        return f"暂未找到「{topic}」相关领域的活跃成员。建议在群里提问。"


@dataclass
class GetCollaboratorsTool(BaseTool):
    """获取协作伙伴工具"""

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
                partner_name = self._resolve_member_name(partner_id)
                strength = collab.get("strength", 0)
                source_name = collab.get("source_name", "")
                context = collab.get("context", "")

                line = f"• {partner_name}（强度 {strength:.0%}，{source_name}"
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
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.load_members()
            for user_id, info in members.items():
                name = info.get("name", "")
                login = info.get("login", "")
                if member_name in [name, login, name.lower(), login.lower()]:
                    return str(user_id)
        return None

    def _resolve_member_name(self, member_id: str) -> str:
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.load_members()
            info = members.get(member_id) or members.get(int(member_id) if member_id.isdigit() else None)
            if info:
                return info.get("name") or info.get("login") or member_id
        return member_id


@dataclass
class SmartCollaborationTool(BaseTool):
    """智能协作推荐工具

    让 Agent 综合分析多个数据源，给出个性化的协作伙伴推荐。
    返回结构化数据，由 Agent 进行推理和个性化回复。
    """

    name: str = "smart_collaboration"
    description: str = (
        "智能推荐协作伙伴。"
        "当用户用自然语言描述协作需求时调用，如："
        "'我想找个会爬虫的人一起做项目'、"
        "'谁能帮我review代码'、"
        "'找个擅长前端的搭档'。"
        "工具会综合分析成员活动轨迹、文档贡献、协作网络，返回结构化数据供你分析。"
        "你需要根据返回的数据，理解用户需求，给出个性化的推荐理由。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "need_description": {
                "type": "string",
                "description": "用户的协作需求描述，如：会爬虫、擅长前端、能帮我review代码"
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "需要的技能列表（可选），如：['Python', '爬虫']"
            },
            "exclude_members": {
                "type": "array",
                "items": {"type": "string"},
                "description": "排除的成员姓名列表（可选）"
            }
        },
        "required": ["need_description"]
    })
    plugin: Any = None

    async def run(
        self,
        event: AstrMessageEvent,
        need_description: str,
        skills: Optional[List[str]] = None,
        exclude_members: Optional[List[str]] = None,
    ) -> str:
        """智能推荐协作伙伴

        返回结构化数据供 Agent 分析。
        """
        try:
            import json

            # 收集数据供 Agent 分析
            data = {
                "need_description": need_description,
                "skills": skills or [],
                "exclude_members": exclude_members or [],
                "candidates": [],
                "context": {
                    "search_keywords": skills or [need_description],
                    "hint": "请根据候选数据，结合用户需求，给出个性化的推荐理由"
                }
            }

            # 1. 从轨迹搜索相关成员
            if hasattr(self.plugin, "trajectory_manager") and self.plugin.trajectory_manager:
                keywords = skills or [need_description]
                for kw in keywords[:3]:
                    try:
                        results = self.plugin.trajectory_manager.search_by_topic(kw, days=60)
                        for r in results[:10]:
                            member_id = r.get("member_id", "")
                            if not member_id:
                                continue
                            member_name = self._resolve_member_name(member_id)
                            # 排除时比较 ID 和姓名
                            if (member_id in (exclude_members or []) or
                                member_name in (exclude_members or [])):
                                continue
                            data["candidates"].append({
                                "member_id": member_id,
                                "member_name": member_name,
                                "source": "trajectory",
                                "keyword": kw,
                                "match_count": r.get("match_count", 0),
                                "matching_events": [
                                    {"title": e.get("title", ""), "type": e.get("event_name", "")}
                                    for e in r.get("matching_events", [])[:3]
                                ],
                                "stats": r.get("stats", {})
                            })
                    except Exception as e:
                        logger.debug(f"[SmartCollaboration] 轨迹搜索失败: {e}")

            # 2. 从文档索引搜索
            if hasattr(self.plugin, "_get_doc_index"):
                doc_index = self.plugin._get_doc_index()
                if doc_index:
                    keywords = skills or [need_description]
                    for kw in keywords[:3]:
                        try:
                            docs = doc_index.search(title=kw, limit=20)
                            for doc in docs:
                                author = doc.get("creator_id") or doc.get("author")
                                if not author:
                                    continue
                                author_str = str(author)
                                author_name = self._resolve_member_name(author_str)
                                # 排除时比较 ID 和姓名
                                if (author_str in (exclude_members or []) or
                                    author_name in (exclude_members or [])):
                                    continue
                                data["candidates"].append({
                                    "member_id": author_str,
                                    "member_name": author_name,
                                    "source": "document",
                                    "keyword": kw,
                                    "doc_title": doc.get("title", ""),
                                    "book_name": doc.get("book_name", "")
                                })
                        except Exception as e:
                            logger.debug(f"[SmartCollaboration] 文档搜索失败: {e}")

            # 3. 去重并合并候选
            unique_candidates: Dict[str, dict] = {}
            for c in data["candidates"]:
                mid = c.get("member_id", "")
                if not mid:
                    continue
                if mid not in unique_candidates:
                    unique_candidates[mid] = c.copy()
                else:
                    existing = unique_candidates[mid]
                    existing["match_count"] = existing.get("match_count", 0) + c.get("match_count", 1)
                    if "keywords" not in existing:
                        existing["keywords"] = []
                    if c.get("keyword") and c.get("keyword") not in existing["keywords"]:
                        existing["keywords"].append(c.get("keyword"))

            # 4. 添加协作网络信息
            for mid, candidate in unique_candidates.items():
                if hasattr(self.plugin, "collaboration_manager") and self.plugin.collaboration_manager:
                    try:
                        stats = self.plugin.collaboration_manager.get_member_stats(mid)
                        candidate["collab_stats"] = stats
                    except Exception:
                        pass

            # 5. 按匹配度排序
            sorted_candidates = sorted(
                unique_candidates.values(),
                key=lambda x: x.get("match_count", 0),
                reverse=True
            )[:10]

            data["candidates"] = sorted_candidates
            data["context"]["total_candidates"] = len(sorted_candidates)

            return json.dumps(data, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"[SmartCollaboration] 执行失败: {e}", exc_info=True)
            return f'{{"error": "{str(e)}"}}'

    def _resolve_member_name(self, member_id: str) -> str:
        if hasattr(self.plugin, "storage") and self.plugin.storage:
            members = self.plugin.storage.load_members()
            info = members.get(member_id) or members.get(int(member_id) if member_id.isdigit() else None)
            if info:
                return info.get("name") or info.get("login") or member_id
        return member_id