"""
NovaBot 用户画像生成模块
基于 LLM 分析用户文档，生成技术画像
"""

from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

from .llm_utils import call_llm, format_docs_for_profile, sanitize_user_input
from .prompts import PROFILE_PROMPT, DOMAIN_ASSESS_PROMPT
from .token_monitor import FEATURE_PROFILE, FEATURE_ASSESS

if TYPE_CHECKING:
    from .token_monitor import TokenMonitor


LEVEL_LABELS = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}


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
        # 清理用户输入
        safe_domain = sanitize_user_input(domain, max_length=50)
        safe_username = sanitize_user_input(username, max_length=50)

        # 筛选相关文档
        domain_docs = self.filter_docs_by_domain(docs, safe_domain)

        if not domain_docs:
            return {
                "domain": safe_domain,
                "level": "未接触",
                "mastered": [],
                "learning": [],
                "gaps": [],
                "next_steps": [f"建议先了解 {safe_domain} 的基础知识"],
                "recommend_resources": [],
            }

        # 构建文档信息
        docs_info = format_docs_for_profile(domain_docs[:10], max_docs=10, max_chars=3000)

        # 调用 LLM 评估
        prompt = DOMAIN_ASSESS_PROMPT.format(
            username=safe_username,
            domain=safe_domain,
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
            result["domain"] = safe_domain
            result["docs_count"] = len(domain_docs)

            return result

        except Exception as e:
            logger.error(f"领域评估失败: {e}")
            return {
                "domain": safe_domain,
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
        for item in learning:
            lines.append(f"• {item}")
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


async def refresh_user_profile(
    *,
    storage,
    profile_generator: ProfileGenerator,
    binding: dict,
    provider,
    docs: list | None = None,
) -> tuple[int, str]:
    """Generate and persist a refreshed user profile.

    Returns ``(docs_count, message)`` so callers can keep their existing
    "正在分析 N 篇文档" progress message before awaiting LLM work.
    """

    yuque_id = binding.get("yuque_id")
    docs = docs if docs is not None else get_profile_docs(storage=storage, binding=binding)
    if not docs:
        return 0, "⚠️ 未找到你的文档，请先执行 /sync 同步"
    if not provider:
        return len(docs), "❌ LLM 未配置，请先配置模型 Provider"

    profile = await profile_generator.generate_with_llm(docs, provider)
    storage.save_profile(yuque_id, profile)
    return len(docs), format_generated_profile_summary(profile)


async def assess_user_domain(
    *,
    storage,
    profile_generator: ProfileGenerator,
    binding: dict,
    domain: str,
    provider,
    docs: list | None = None,
) -> tuple[int, str]:
    """Assess a bound user's level in one domain and format the result."""

    docs = docs if docs is not None else get_profile_docs(storage=storage, binding=binding)
    if not docs:
        return 0, "⚠️ 未找到你的文档，请先执行 /sync 同步"
    if not provider:
        return len(docs), "❌ LLM 未配置，请先配置模型 Provider"

    assessment = await profile_generator.assess_domain_level(docs, domain, provider)
    return len(docs), format_domain_assessment(assessment)


def get_profile_docs(*, storage, binding: dict) -> list:
    """Load docs for a bound Yuque user using id-first matching."""

    return storage.get_docs_by_author(binding.get("yuque_name", ""), binding.get("yuque_id"))


def format_generated_profile_summary(profile: dict) -> str:
    """Format the short response after refreshing a user's profile."""

    p = profile.get("profile", {})
    return (
        "✅ 画像已生成\n"
        "━━━━━━━━━━━━━━━\n"
        f"兴趣: {', '.join(_string_list(p.get('interests')))}\n"
        f"水平: {LEVEL_LABELS.get(p.get('level', ''), '未知')}\n"
        f"标签: {', '.join(_string_list(p.get('tags')))}\n"
        "\n"
        f"📝 {p.get('summary', '')}"
    )


def format_profile_view(*, binding: dict, profile: dict | None) -> str:
    """Format the /profile view without triggering LLM generation."""

    yuque_name = binding.get("yuque_name", "")
    yuque_login = binding.get("yuque_login", "")
    if not profile:
        return (
            "📋 用户画像\n"
            "━━━━━━━━━━━━━━━\n"
            f"账号: @{yuque_login} ({yuque_name})\n"
            "\n"
            "画像未生成\n"
            "使用 /profile refresh 生成画像"
        )

    p = profile.get("profile", {})
    stats = profile.get("stats", {})
    skill_lines = _profile_skill_lines(p)
    repos = stats.get("repos", [])
    repos_str = ", ".join(repos[:3])
    if len(repos) > 3:
        repos_str += f" 等 {len(repos)} 个"

    lines = [
        "📋 用户画像",
        "━━━━━━━━━━━━━━━",
        f"账号: @{yuque_login} ({yuque_name})",
        "",
        "🎯 兴趣领域",
    ]
    lines.extend(skill_lines or ["暂无数据"])

    tags = _string_list(p.get("tags"))
    if tags:
        lines.extend(["", "🏷️ 标签", f"• {' • '.join(tags)}"])

    lines.extend(
        [
            "",
            "📊 统计",
            f"• 文档数: {stats.get('docs_count', 0)} 篇",
            f"• 知识库: {repos_str or '暂无'}",
            f"• 整体水平: {LEVEL_LABELS.get(p.get('level', ''), '未知')}",
        ]
    )

    summary = p.get("summary", "")
    if summary:
        lines.extend(["", f"📝 {summary}"])

    lines.extend(["", "💡 使用 /profile refresh 重新分析"])
    return "\n".join(lines)


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _profile_skill_lines(profile: dict) -> list[str]:
    skills = profile.get("skills", {})
    lines = []
    for interest in profile.get("interests", []):
        skill_level = skills.get(interest)
        if skill_level:
            lines.append(f"• {interest} ({LEVEL_LABELS.get(skill_level, '入门')})")
            continue

        interest_lower = interest.lower()
        matched_level = ""
        for skill_name, level in skills.items():
            skill_lower = skill_name.lower()
            if interest_lower in skill_lower or skill_lower in interest_lower:
                matched_level = level
                break
        lines.append(f"• {interest} ({LEVEL_LABELS.get(matched_level, '入门')})")
    return lines
