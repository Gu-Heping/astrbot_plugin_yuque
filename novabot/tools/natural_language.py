"""
NovaBot 自然语言交互工具
将部分指令功能开放为 Agent 可调用的工具
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .base import BaseTool


@dataclass
class PartnerRecommendTool(BaseTool):
    """伙伴推荐工具

    当用户说"推荐学习伙伴"、"谁也在学XX"时调用
    """

    name: str = "partner_recommend"
    description: str = "推荐学习伙伴或导师。当用户说'推荐学习伙伴'、'谁也在学XX'、'找个导师'等时调用。可选指定主题领域。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "可选的主题领域，如'爬虫'、'Python'。不指定则根据用户兴趣推荐。"
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, topic: str = "") -> str:
        if not self.plugin:
            return "插件未初始化"

        # 获取用户绑定信息
        platform_id = event.get_sender_id()
        binding = self.plugin.storage.get_binding(platform_id)

        if not binding:
            return "请先绑定账号后再使用伙伴推荐功能。使用 /bind <用户名> 绑定。"

        yuque_id = binding.get("yuque_id")
        if not yuque_id:
            return "绑定信息异常，请重新绑定"

        # 检查是否有画像
        profile = self.plugin.storage.load_profile(yuque_id)
        if not profile:
            return "您还没有用户画像，请先使用 /profile refresh 生成画像。"

        # 使用 PartnerMatcher 推荐
        from ..partner import PartnerMatcher, format_partner_result

        matcher = PartnerMatcher(self.plugin.storage)

        # 找学习伙伴
        partners = matcher.find_partners(yuque_id, topic=topic if topic else None, limit=5)

        # 找导师
        mentors = matcher.find_mentors(yuque_id, topic=topic if topic else None, limit=3)

        if not partners and not mentors:
            if topic:
                return f"暂无在「{topic}」领域的学习伙伴或导师"
            return "暂无匹配的学习伙伴或导师，可能是社团成员画像数据不足"

        result = format_partner_result(partners, mentors, topic if topic else None, storage=self.plugin.storage)
        return result


@dataclass
class LearningPathTool(BaseTool):
    """学习路径推荐工具

    当用户说"我想学XX"、"推荐学习路径"时调用
    """

    name: str = "learning_path"
    description: str = "生成学习路径推荐。当用户说'我想学XX'、'怎么入门XX'、'推荐学习路径'等时调用。需要指定目标领域。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "目标学习领域，如'爬虫'、'Python'、'前端开发'等"
            }
        },
        "required": ["domain"]
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, domain: str) -> str:
        if not self.plugin:
            return "插件未初始化"

        if not domain:
            return "请指定学习领域，如：爬虫、Python、前端开发"

        # 获取 LLM Provider
        provider = self.plugin.context.get_using_provider(umo=event.unified_msg_origin)
        if not provider:
            return "LLM 未配置，无法生成学习路径"

        # 获取用户绑定信息
        platform_id = event.get_sender_id()
        binding = self.plugin.storage.get_binding(platform_id)

        profile = None
        exclude_author_id = None
        exclude_author_name = None
        user_docs = None

        if binding:
            yuque_id = binding.get("yuque_id")
            yuque_name = binding.get("yuque_name", "")

            profile = self.plugin.storage.load_profile(yuque_id)

            # 获取用户已写文档（用于排除）
            if yuque_id:
                doc_index = self.get_doc_index()
                if doc_index:
                    try:
                        conn = doc_index._get_conn()
                        rows = conn.execute("""
                            SELECT title FROM docs
                            WHERE creator_id = ?
                            ORDER BY word_count DESC
                            LIMIT 20
                        """, (yuque_id,)).fetchall()
                        user_docs = [{"title": r["title"]} for r in rows]
                    except Exception:
                        pass

            exclude_author_id = yuque_id
            exclude_author_name = yuque_name

        # 使用 LearningPathRecommender 生成
        from ..learning_path import LearningPathRecommender, format_learning_path

        recommender = LearningPathRecommender(
            storage=self.plugin.storage,
            rag=self.plugin.rag,
            token_monitor=self.plugin.token_monitor,
        )

        path = await recommender.recommend(
            profile=profile,
            target_domain=domain,
            provider=provider,
            exclude_author_id=exclude_author_id,
            exclude_author_name=exclude_author_name,
            user_docs=user_docs,
        )

        return format_learning_path(path)


@dataclass
class WeeklyReportTool(BaseTool):
    """周报生成工具

    当用户说"本周周报"、"最近更新了什么"时调用
    """

    name: str = "weekly_report"
    description: str = "生成本周知识周报。当用户说'本周周报'、'最近更新了什么'、'社团最近动态'等时调用。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent) -> str:
        if not self.plugin:
            return "插件未初始化"

        docs_dir = self.plugin.storage.docs_dir
        doc_index = self.get_doc_index()

        # 获取 LLM Provider（可选）
        provider = self.plugin.context.get_using_provider(umo=event.unified_msg_origin)

        from ..weekly import WeeklyReporter

        reporter = WeeklyReporter(docs_dir, doc_index=doc_index)

        if provider:
            # 生成带 LLM 分析的周报
            report = await reporter.generate_weekly_report_with_llm(
                provider=provider,
                token_monitor=self.plugin.token_monitor,
            )
        else:
            # 生成纯统计周报
            report = reporter.generate_weekly_report()

        return report


@dataclass
class KnowledgeGapTool(BaseTool):
    """学习缺口分析工具

    当用户说"我的学习缺口"、"还差什么"时调用
    """

    name: str = "knowledge_gap"
    description: str = "分析个人学习缺口。当用户说'我的学习缺口'、'还差什么知识'、'哪里需要补充'等时调用。可选指定目标领域，不指定则自动推断。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "可选的目标领域。不指定则根据用户兴趣自动推断。"
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, domain: str = "") -> str:
        if not self.plugin:
            return "插件未初始化"

        # 获取用户绑定信息
        platform_id = event.get_sender_id()
        binding = self.plugin.storage.get_binding(platform_id)

        if not binding:
            return "请先绑定账号后才能分析学习缺口。使用 /bind <用户名> 绑定。"

        yuque_id = binding.get("yuque_id")
        if not yuque_id:
            return "绑定信息异常，请重新绑定"

        # 获取 LLM Provider
        provider = self.plugin.context.get_using_provider(umo=event.unified_msg_origin)
        if not provider:
            return "LLM 未配置，无法分析学习缺口"

        # 使用 LearningGapAnalyzer 分析
        from ..knowledge_gap import LearningGapAnalyzer, format_gap_report

        analyzer = LearningGapAnalyzer(
            storage=self.plugin.storage,
            rag=self.plugin.rag,
            token_monitor=self.plugin.token_monitor,
        )

        target_domain = domain if domain else None
        gap = await analyzer.analyze(
            yuque_id=yuque_id,
            target_domain=target_domain,
            provider=provider,
        )

        return format_gap_report(gap)


@dataclass
class SubscribeTool(BaseTool):
    """订阅工具

    当用户说"订阅XX知识库"、"关注XX作者"时调用
    """

    name: str = "subscribe"
    description: str = "订阅知识库或作者更新。当用户说'订阅XX知识库'、'关注XX作者'、'订阅全部更新'等时调用。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "sub_type": {
                "type": "string",
                "description": "订阅类型：repo（知识库）、author（作者）、all（全部）"
            },
            "target": {
                "type": "string",
                "description": "订阅目标：知识库名或作者名。订阅类型为'all'时不需要。"
            }
        },
        "required": ["sub_type"]
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, sub_type: str, target: str = "") -> str:
        if not self.plugin:
            return "插件未初始化"

        if sub_type not in ("repo", "author", "all"):
            return f"无效的订阅类型：{sub_type}。可选：repo（知识库）、author（作者）、all（全部）"

        if sub_type != "all" and not target:
            return f"订阅类型 {sub_type} 需要指定目标（知识库名或作者名）"

        platform_id = event.get_sender_id()
        umo = event.unified_msg_origin

        # 使用 SubscriptionManager 订阅
        success, msg = await self.plugin.subscribe_mgr.subscribe(
            platform_id=platform_id,
            umo=umo,
            sub_type=sub_type,
            target=target if sub_type != "all" else None,
        )

        return f"{'✅' if success else '❌'} {msg}"


@dataclass
class UnsubscribeTool(BaseTool):
    """取消订阅工具

    当用户说"取消订阅"时调用
    """

    name: str = "unsubscribe"
    description: str = "取消订阅。当用户说'取消订阅'、'不再关注'等时调用。可以取消特定订阅或全部订阅。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "sub_id": {
                "type": "string",
                "description": "订阅 ID。不指定则取消该会话的所有订阅。"
            }
        },
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, sub_id: str = "") -> str:
        if not self.plugin:
            return "插件未初始化"

        platform_id = event.get_sender_id()
        umo = event.unified_msg_origin

        # 使用 SubscriptionManager 取消订阅
        sub_id_int = None
        if sub_id:
            try:
                sub_id_int = int(sub_id)
            except ValueError:
                return f"无效的订阅 ID：{sub_id}，请输入数字"

        success, msg = await self.plugin.subscribe_mgr.unsubscribe(
            platform_id=platform_id,
            umo=umo,
            sub_id=sub_id_int,
        )

        return f"{'✅' if success else '❌'} {msg}"


@dataclass
class ProfileViewTool(BaseTool):
    """查看用户画像工具

    当用户说"看看我的画像"、"我的学习情况"时调用
    """

    name: str = "profile_view"
    description: str = "查看用户画像。当用户说'看看我的画像'、'我的学习情况'、'我的兴趣是什么'等时调用。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": []
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent) -> str:
        if not self.plugin:
            return "插件未初始化"

        platform_id = event.get_sender_id()
        binding = self.plugin.storage.get_binding(platform_id)

        if not binding:
            return "您还未绑定账号。使用 /bind <用户名> 绑定后可查看画像。"

        yuque_id = binding.get("yuque_id")
        yuque_name = binding.get("yuque_name", "未知")

        profile = self.plugin.storage.load_profile(yuque_id)

        if not profile:
            return f"您（{yuque_name}）还没有用户画像。\n使用 /profile refresh 生成画像。"

        # 格式化画像信息
        p = profile.get("profile", {})
        stats = profile.get("stats", {})

        interests = p.get("interests", [])
        level = p.get("level", "unknown")
        tags = p.get("tags", [])
        summary = p.get("summary", "")
        skills = p.get("skills", {})

        level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}

        lines = [f"📊 {yuque_name} 的用户画像", ""]

        lines.append(f"🎯 整体水平：{level_map.get(level, level)}")
        lines.append("")

        if interests:
            lines.append("💡 兴趣领域")
            lines.append(f"  {', '.join(interests)}")
            lines.append("")

        if tags:
            lines.append("🏷️ 标签")
            lines.append(f"  {', '.join(tags)}")
            lines.append("")

        if skills:
            lines.append("🔧 技能评估")
            for skill, lvl in list(skills.items())[:10]:
                lvl_text = level_map.get(lvl, lvl)
                lines.append(f"  • {skill}: {lvl_text}")
            lines.append("")

        if summary:
            lines.append("📝 概括")
            lines.append(f"  {summary}")
            lines.append("")

        if stats:
            docs_count = stats.get("docs_count", 0)
            total_words = stats.get("total_words", 0)
            if docs_count or total_words:
                lines.append("📈 学习统计")
                lines.append(f"  • 文档数：{docs_count}")
                lines.append(f"  • 总字数：{total_words}")
                lines.append("")

        lines.append("─" * 20)
        lines.append("使用 /profile refresh 刷新画像")
        lines.append("使用 /profile assess <领域> 评估领域水平")

        return "\n".join(lines)