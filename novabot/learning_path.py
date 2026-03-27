"""
NovaBot 学习路径推荐模块
根据用户水平和目标领域生成学习计划
"""

from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

from .llm_utils import call_llm, format_resources_for_path
from .prompts import PATH_PROMPT, PATH_FALLBACK_PROMPT
from .token_monitor import FEATURE_LEARNING_PATH

if TYPE_CHECKING:
    from .rag import RAGEngine
    from .storage import Storage
    from .token_monitor import TokenMonitor


class LearningPathRecommender:
    """学习路径推荐器"""

    def __init__(
        self,
        storage: "Storage",
        rag: Optional["RAGEngine"] = None,
        token_monitor: Optional["TokenMonitor"] = None,
    ):
        """初始化推荐器

        Args:
            storage: Storage 实例
            rag: RAG 引擎（可选）
            token_monitor: Token 监控器（可选）
        """
        self.storage = storage
        self.rag = rag
        self.token_monitor = token_monitor

    def get_user_level(self, profile: dict, domain: str) -> str:
        """获取用户在特定领域的水平

        Args:
            profile: 用户画像
            domain: 目标领域

        Returns:
            水平等级
        """
        if not profile:
            return "beginner"

        p = profile.get("profile", {})
        skills = p.get("skills", {})

        # 检查是否有该领域的技能评级
        domain_lower = domain.lower()
        for skill, level in skills.items():
            if domain_lower in skill.lower() or skill.lower() in domain_lower:
                return level

        # 返回整体水平
        return p.get("level", "beginner")

    def get_mastered_skills(self, profile: dict) -> list:
        """获取用户已掌握的技能

        Args:
            profile: 用户画像

        Returns:
            技能列表
        """
        if not profile:
            return []

        p = profile.get("profile", {})
        skills = p.get("skills", {})

        # 返回 intermediate 及以上水平的技能
        return [
            skill for skill, level in skills.items()
            if level in ("intermediate", "advanced")
        ]

    def search_resources(self, domain: str, max_results: int = 10) -> list:
        """搜索相关资源

        Args:
            domain: 目标领域
            max_results: 最大结果数

        Returns:
            资源列表
        """
        resources = []

        # 从 RAG 搜索
        if self.rag:
            try:
                results = self.rag.search(domain, k=max_results)
                for r in results:
                    resources.append({
                        "title": r.get("title", ""),
                        "author": r.get("author", ""),
                        "book_name": r.get("book_name", ""),
                    })
            except Exception as e:
                logger.warning(f"RAG 搜索失败: {e}")

        return resources

    async def recommend(
        self,
        profile: dict,
        target_domain: str,
        provider
    ) -> dict:
        """生成学习路径

        Args:
            profile: 用户画像
            target_domain: 目标领域
            provider: LLM Provider

        Returns:
            学习路径字典
        """
        # 获取用户信息
        current_level = self.get_user_level(profile, target_domain)
        mastered_skills = self.get_mastered_skills(profile)

        # 搜索相关资源
        resources = self.search_resources(target_domain)
        resources_text = format_resources_for_path(resources)

        # 选择提示词模板
        if resources:
            prompt = PATH_PROMPT.format(
                current_level=current_level,
                mastered_skills=", ".join(mastered_skills) if mastered_skills else "暂无",
                target_domain=target_domain,
                resources=resources_text
            )
        else:
            prompt = PATH_FALLBACK_PROMPT.format(target_domain=target_domain)

        try:
            result = await call_llm(
                provider=provider,
                prompt=prompt,
                system_prompt="你是一个学习规划顾问，善于根据学习者现状设计高效路径。",
                require_json=True,
                token_monitor=self.token_monitor,
                feature=FEATURE_LEARNING_PATH,
            )
            result["target_domain"] = target_domain
            result["current_level"] = current_level

            return result

        except Exception as e:
            logger.error(f"学习路径生成失败: {e}")
            return {
                "target_domain": target_domain,
                "current_level": current_level,
                "error": f"生成失败: {e}"
            }


def format_learning_path(path: dict) -> str:
    """格式化学习路径

    Args:
        path: 学习路径字典

    Returns:
        格式化的文本
    """
    if path.get("error"):
        return f"❌ {path['error']}"

    domain = path.get("target_domain", "未知领域")
    current_level = path.get("current_level", "beginner")

    level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}
    level_text = level_map.get(current_level, current_level)

    lines = [f"🎯 学习路径：{domain}"]
    lines.append(f"当前水平：{level_text}")
    lines.append("")

    # 差距分析
    gap_analysis = path.get("gap_analysis", "")
    if gap_analysis:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📊 差距分析")
        lines.append("")
        lines.append(gap_analysis)
        lines.append("")

    # 各阶段
    stages = path.get("stages", [])
    if stages:
        for stage in stages:
            stage_num = stage.get("stage", 1)
            focus = stage.get("focus", stage.get("title", f"阶段 {stage_num}"))
            duration = stage.get("duration", "")
            goals = stage.get("goals", [])
            challenges = stage.get("challenges", stage.get("tasks", []))
            resources = stage.get("resources", [])

            lines.append(f"━━━━━━━━━━━━━━━")
            lines.append(f"📚 阶段 {stage_num}：{focus}" + (f"（{duration}）" if duration else ""))
            lines.append("")

            if goals:
                lines.append("🎯 学习目标")
                for g in goals:
                    lines.append(f"• {g}")
                lines.append("")

            if resources:
                lines.append("📖 推荐资源")
                for r in resources:
                    lines.append(f"• {r}")
                lines.append("")

            if challenges:
                lines.append("⚠️ 可能的挑战")
                for c in challenges:
                    lines.append(f"• {c}")
                lines.append("")

    # 里程碑
    milestones = path.get("milestones", [])
    if milestones:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("🏁 阶段性成果")
        lines.append("")
        for m in milestones:
            lines.append(f"✅ {m}")
        lines.append("")

    # 建议
    tips = path.get("tips", "")
    if tips:
        lines.append(f"💡 {tips}")

    return "\n".join(lines)