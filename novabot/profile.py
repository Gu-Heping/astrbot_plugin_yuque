"""
NovaBot 用户画像生成模块
基于 LLM 分析用户文档，生成技术画像
"""

from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

from .llm_utils import call_llm, extract_json, format_docs_for_profile
from .prompts import PROFILE_PROMPT, DOMAIN_ASSESS_PROMPT
from .token_monitor import FEATURE_PROFILE, FEATURE_ASSESS

if TYPE_CHECKING:
    from .token_monitor import TokenMonitor


class ProfileGenerator:
    """用户画像生成器（LLM 驱动）"""

    def __init__(self, token_monitor: Optional["TokenMonitor"] = None):
        self.token_monitor = token_monitor

    def build_docs_info(self, docs: list) -> str:
        """构建文档信息字符串"""
        return format_docs_for_profile(docs, max_docs=30, max_chars=5000)

    def _normalize_level(self, level: str) -> str:
        """标准化水平值（支持中英文）"""
        mapping = {
            "beginner": "beginner", "入门": "beginner", "初级": "beginner",
            "intermediate": "intermediate", "进阶": "intermediate", "中级": "intermediate",
            "advanced": "advanced", "高级": "advanced",
        }
        return mapping.get(level.lower() if level else "", "beginner")

    async def generate_with_llm(self, docs: list, provider) -> dict:
        """使用 LLM 生成用户画像

        Args:
            docs: 文档列表
            provider: AstrBot LLM Provider

        Returns:
            画像字典
        """
        if not docs:
            return self._empty_profile()

        docs_info = self.build_docs_info(docs)
        prompt = PROFILE_PROMPT.format(docs_info=docs_info)

        try:
            profile_data = await call_llm(
                provider=provider,
                prompt=prompt,
                system_prompt="你是一个技术能力分析师，善于从文档中读懂一个人的技术成长轨迹。",
                require_json=True,
                token_monitor=self.token_monitor,
                feature=FEATURE_PROFILE,
            )

            # 标准化水平值
            normalized_skills = {
                k: self._normalize_level(v)
                for k, v in profile_data.get("skills", {}).items()
            }

            # 确保 skills 的 key 与 interests 一致
            interests = profile_data.get("interests", [])
            aligned_skills = {}
            for interest in interests:
                # 精确匹配
                if interest in normalized_skills:
                    aligned_skills[interest] = normalized_skills[interest]
                    continue

                # 模糊匹配
                interest_lower = interest.lower()
                matched = False
                for skill_name, level in normalized_skills.items():
                    skill_lower = skill_name.lower()
                    if interest_lower in skill_lower or skill_lower in interest_lower:
                        aligned_skills[interest] = level
                        matched = True
                        break

                if not matched:
                    # 没有匹配到，设置默认值
                    aligned_skills[interest] = "beginner"

            # 构建返回格式
            return {
                "profile": {
                    "interests": interests,
                    "level": self._normalize_level(profile_data.get("level", "beginner")),
                    "skills": aligned_skills,
                    "tags": profile_data.get("tags", []),
                    "summary": profile_data.get("summary", ""),
                    "trajectory": profile_data.get("trajectory", ""),
                    "style": profile_data.get("style", ""),
                },
                "stats": {
                    "docs_count": len(docs),
                    "repos": list(set(doc.get("book_name", "") for doc in docs if doc.get("book_name"))),
                }
            }

        except Exception as e:
            logger.error(f"LLM 生成画像失败: {e}")
            return self._empty_profile()

    def _empty_profile(self) -> dict:
        return {
            "profile": {
                "interests": [],
                "level": "beginner",
                "skills": {},
                "tags": [],
                "summary": "",
                "trajectory": "",
                "style": "",
            },
            "stats": {"docs_count": 0, "repos": []}
        }

    # ========== 领域认知评估 ==========

    def filter_docs_by_domain(self, docs: list, domain: str) -> list:
        """筛选与特定领域相关的文档

        Args:
            docs: 文档列表
            domain: 领域关键词

        Returns:
            相关文档列表
        """
        domain_lower = domain.lower()
        related_docs = []

        for doc in docs:
            title = doc.get("title", "").lower()
            content = doc.get("content", "").lower() if doc.get("content") else ""
            book_name = doc.get("book_name", "").lower()

            # 检查标题、内容、知识库名是否包含领域关键词
            if (domain_lower in title or
                domain_lower in content[:500] or
                domain_lower in book_name):
                related_docs.append(doc)

        return related_docs

    async def assess_domain_level(
        self,
        docs: list,
        domain: str,
        provider,
        username: str = "用户"
    ) -> dict:
        """评估用户在特定领域的水平

        Args:
            docs: 用户所有文档
            domain: 要评估的领域
            provider: LLM Provider
            username: 用户名

        Returns:
            领域评估结果
        """
        # 筛选相关文档
        domain_docs = self.filter_docs_by_domain(docs, domain)

        if not domain_docs:
            return {
                "domain": domain,
                "level": "未接触",
                "mastered": [],
                "learning": [],
                "gaps": [],
                "next_steps": [f"建议先了解 {domain} 的基础知识"],
                "recommend_resources": [],
            }

        # 构建文档信息
        docs_info = format_docs_for_profile(domain_docs[:10], max_docs=10, max_chars=3000)

        # 调用 LLM 评估
        prompt = DOMAIN_ASSESS_PROMPT.format(
            username=username,
            domain=domain,
            domain_docs=docs_info
        )

        try:
            result = await call_llm(
                provider=provider,
                prompt=prompt,
                system_prompt="你是一个技术能力评估专家，善于判断学习者在特定领域的掌握程度。",
                require_json=True,
                token_monitor=self.token_monitor,
                feature=FEATURE_ASSESS,
            )

            # 标准化水平值
            result["level"] = self._normalize_level(result.get("level", "beginner"))
            result["domain"] = domain
            result["docs_count"] = len(domain_docs)

            return result

        except Exception as e:
            logger.error(f"领域评估失败: {e}")
            return {
                "domain": domain,
                "level": "未知",
                "mastered": [],
                "learning": [],
                "gaps": [],
                "next_steps": [],
                "recommend_resources": [],
            }


def format_domain_assessment(assessment: dict) -> str:
    """格式化领域评估结果

    Args:
        assessment: 评估结果字典

    Returns:
        格式化的文本
    """
    domain = assessment.get("domain", "未知领域")
    level = assessment.get("level", assessment.get("current_level", "未知"))

    level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}
    level_text = level_map.get(level, level)

    lines = [f"📊 {domain} 领域评估：{level_text}"]
    lines.append("")

    # 已掌握
    mastered = assessment.get("mastered", [])
    if mastered:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("✅ 已掌握")
        for m in mastered:
            lines.append(f"• {m}")
        lines.append("")

    # 正在学习
    learning = assessment.get("learning", [])
    if learning:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📖 正在学习")
        for l in learning:
            lines.append(f"• {l}")
        lines.append("")

    # 知识缺口
    gaps = assessment.get("gaps", [])
    if gaps:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("⚠️ 知识缺口")
        for g in gaps:
            lines.append(f"• {g}")
        lines.append("")

    # 下一步建议
    next_steps = assessment.get("next_steps", [])
    if next_steps:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("🎯 建议下一步")
        for n in next_steps:
            lines.append(f"• {n}")
        lines.append("")

    # 推荐资源
    resources = assessment.get("recommend_resources", [])
    if resources:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📚 推荐学习")
        for r in resources:
            lines.append(f"• {r}")

    return "\n".join(lines)