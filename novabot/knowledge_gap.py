"""
NovaBot 学习缺口分析模块
分析用户个人学习中的知识缺口
"""

from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

from .llm_utils import call_llm, format_resources_for_path
from .prompts import GAP_PROMPT, GAP_NO_BINDING_PROMPT, GAP_NO_PROFILE_PROMPT, GAP_NO_TARGET_PROMPT
from .token_monitor import FEATURE_LEARNING_PATH

if TYPE_CHECKING:
    from .rag import RAGEngine
    from .storage import Storage
    from .doc_index import DocIndex
    from .token_monitor import TokenMonitor


class LearningGapAnalyzer:
    """学习缺口分析器

    分析用户个人学习中的知识缺口：
    - 用户想学的 vs 用户已掌握的
    - 结合社团知识库，找出缺少的知识点
    - 给出补充建议
    """

    def __init__(
        self,
        storage: "Storage",
        rag: Optional["RAGEngine"] = None,
        token_monitor: Optional["TokenMonitor"] = None,
    ):
        self.storage = storage
        self.rag = rag
        self.token_monitor = token_monitor

        # 初始化 DocIndex
        from .doc_index import DocIndex
        self.doc_index = DocIndex(storage.data_dir / "doc_index.db")

    async def analyze(
        self,
        yuque_id: str,
        target_domain: Optional[str] = None,
        provider=None,
    ) -> dict:
        """分析用户学习缺口

        Args:
            yuque_id: 语雀用户 ID
            target_domain: 目标领域（可选，不指定则自动推断）
            provider: LLM Provider

        Returns:
            缺口分析结果字典
        """
        # 1. 获取用户画像
        profile = self.storage.load_profile(yuque_id)
        if not profile:
            return {
                "error": "需要生成画像",
                "message": "使用 /profile refresh 生成用户画像后，才能分析学习缺口。"
            }

        profile_data = profile.get("profile", {})
        interests = profile_data.get("interests", [])
        skills = profile_data.get("skills", {})
        level = profile_data.get("level", "beginner")

        # 2. 获取用户已写文档
        user_docs = self._get_user_docs(yuque_id)
        user_docs_text = self._format_user_docs(user_docs)

        # 3. 如果没有指定目标领域，需要先推断
        if not target_domain:
            if not interests:
                return {
                    "error": "无法推断目标",
                    "message": "你的画像中没有兴趣领域，请指定目标领域：/gap <领域>"
                }

            # 让 LLM 推断最值得分析的目标领域
            try:
                suggest_result = await call_llm(
                    provider=provider,
                    prompt=GAP_NO_TARGET_PROMPT.format(interests=", ".join(interests)),
                    require_json=True,
                )
                target_domain = suggest_result.get("suggested_target", interests[0])
                logger.info(f"[GapAnalyzer] 推断目标领域: {target_domain}")
            except Exception as e:
                logger.warning(f"[GapAnalyzer] 推断目标失败: {e}, 使用第一个兴趣")
                target_domain = interests[0]

        # 4. 获取用户在目标领域的水平
        current_level = self._get_domain_level(skills, target_domain, level)

        # 5. 获取已掌握技能
        mastered_skills = [
            skill for skill, lvl in skills.items()
            if lvl in ("intermediate", "advanced")
        ]

        # 6. 搜索社团相关资源（排除用户自己的文档）
        # 合并查询，避免重复调用
        binding = self.storage.get_binding_by_yuque_id(yuque_id)
        author_name = binding.get("yuque_name", "") if binding else ""
        # 绑定信息中存的是 yuque_id，不是 yuque_user_id
        creator_id = binding.get("yuque_id") if binding else None

        logger.debug(f"[GapAnalyzer] 用户: {author_name}, 目标领域: {target_domain}, 用户文档数: {len(user_docs)}")

        community_resources = self._search_resources(
            target_domain,
            exclude_author_id=creator_id,
            exclude_author_name=author_name,
            exclude_titles=[d["title"] for d in user_docs],
        )
        logger.debug(f"[GapAnalyzer] 社团资源（排除用户文档后）: {len(community_resources)} 篇")
        community_resources_text = format_resources_for_path(community_resources)

        # 7. 调用 LLM 分析缺口
        prompt = GAP_PROMPT.format(
            user_name=author_name or "未知用户",
            target_domain=target_domain,
            current_level=current_level,
            interests=", ".join(interests) if interests else "暂无",
            mastered_skills=", ".join(mastered_skills) if mastered_skills else "暂无",
            user_docs=user_docs_text,
            community_resources=community_resources_text,
        )

        try:
            result = await call_llm(
                provider=provider,
                prompt=prompt,
                system_prompt="你是一个学习诊断专家，善于分析知识缺口并给出补充建议。",
                require_json=True,
                token_monitor=self.token_monitor,
                feature=FEATURE_LEARNING_PATH,
            )
            result["target_domain"] = target_domain
            result["current_level"] = current_level
            return result

        except Exception as e:
            logger.error(f"[GapAnalyzer] 分析失败: {e}")
            return {
                "target_domain": target_domain,
                "error": f"分析失败: {e}"
            }

    def _get_user_docs(self, yuque_id: str) -> list:
        """获取用户已写的文档列表

        Args:
            yuque_id: 语雀用户 ID

        Returns:
            文档列表
        """
        docs = []

        if self.doc_index:
            try:
                binding = self.storage.get_binding_by_yuque_id(yuque_id)
                author_name = binding.get("yuque_name", "") if binding else ""
                # 绑定信息中存的是 yuque_id，SQLite 中存的也是 yuque_id (creator_id)
                creator_id = binding.get("yuque_id") if binding else None

                conn = self.doc_index._get_conn()

                # 优先用 creator_id 精确匹配
                if creator_id:
                    rows = conn.execute("""
                        SELECT title, book_name, word_count
                        FROM docs
                        WHERE creator_id = ?
                        ORDER BY word_count DESC
                        LIMIT 20
                    """, (creator_id,)).fetchall()
                else:
                    # 回退到精确名称匹配
                    rows = conn.execute("""
                        SELECT title, book_name, word_count
                        FROM docs
                        WHERE author = ?
                        ORDER BY word_count DESC
                        LIMIT 20
                    """, (author_name,)).fetchall()

                for row in rows:
                    docs.append({
                        "title": row["title"] or "",
                        "book_name": row["book_name"] or "",
                        "word_count": row["word_count"] or 0,
                    })

            except Exception as e:
                logger.warning(f"[GapAnalyzer] 获取用户文档失败: {e}")

        return docs

    def _format_user_docs(self, docs: list) -> str:
        """格式化用户文档列表

        Args:
            docs: 文档列表

        Returns:
            格式化的文本
        """
        if not docs:
            return "暂无已写文档"

        lines = []
        for i, doc in enumerate(docs[:10], 1):
            title = doc["title"]
            words = doc["word_count"]
            lines.append(f"{i}. 《{title}》（{words}字）")

        return "\n".join(lines)

    def _get_domain_level(self, skills: dict, domain: str, default_level: str) -> str:
        """获取用户在特定领域的水平

        Args:
            skills: 技能字典
            domain: 目标领域
            default_level: 默认水平

        Returns:
            水平等级
        """
        domain_lower = domain.lower()
        for skill, level in skills.items():
            if domain_lower in skill.lower() or skill.lower() in domain_lower:
                return level
        return default_level

    def _search_resources(
        self,
        domain: str,
        max_results: int = 15,
        exclude_author_id=None,
        exclude_author_name: Optional[str] = None,
        exclude_titles: Optional[list] = None,
    ) -> list:
        """搜索社团相关资源

        Args:
            domain: 目标领域
            max_results: 最大结果数
            exclude_author_id: 排除的作者 ID（可能是 int 或 str）
            exclude_author_name: 排除的作者名
            exclude_titles: 排除的标题列表

        Returns:
            资源列表
        """
        resources = []
        exclude_titles = exclude_titles or []

        if self.rag:
            try:
                results = self.rag.search(domain, k=max_results * 2)
                for r in results:
                    title = r.get("title", "")
                    author = r.get("author", "")
                    author_id = r.get("creator_id")

                    # 通过 ID 或名称排除用户自己的文档（统一字符串比较）
                    if exclude_author_id and str(author_id) == str(exclude_author_id):
                        continue
                    if exclude_author_name and exclude_author_name in author:
                        continue
                    if title in exclude_titles:
                        continue

                    resources.append({
                        "title": title,
                        "author": r.get("author", ""),
                        "book_name": r.get("book_name", ""),
                    })

                    if len(resources) >= max_results:
                        break

            except Exception as e:
                logger.warning(f"[GapAnalyzer] 资源搜索失败: {e}")

        return resources


def format_gap_report(gap: dict) -> str:
    """格式化缺口分析报告

    Args:
        gap: 缺口分析字典

    Returns:
        格式化的文本
    """
    # 错误情况
    if gap.get("error"):
        msg = gap.get("message", gap.get("error"))
        return f"❌ {msg}"

    target = gap.get("target_domain", "未知领域")
    current_level = gap.get("current_level", "beginner")

    level_map = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}
    level_text = level_map.get(current_level, current_level)

    lines = [f"📊 学习缺口分析：{target}"]
    lines.append(f"🎯 当前水平：{level_text}")
    lines.append("")

    # 已掌握知识点
    mastered = gap.get("mastered_topics", [])
    if mastered:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("✅ 已掌握的知识")
        lines.append("")
        for m in mastered[:10]:
            topic = m.get("topic", "")
            source = m.get("source", "")
            lines.append(f"• {topic}" + (f"（来自：{source}）" if source else ""))
        lines.append("")

    # 缺少的知识点
    missing = gap.get("missing_topics", [])
    if missing:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("❌ 缺少的知识点")
        lines.append("")
        for m in missing[:10]:
            topic = m.get("topic", "")
            priority = m.get("priority", "medium")
            reason = m.get("reason", "")
            icon = "🔴" if priority == "high" else ("🟡" if priority == "medium" else "🟢")
            lines.append(f"{icon} {topic}" + (f" - {reason}" if reason else ""))
        lines.append("")

    # 推荐资源
    resources = gap.get("recommended_resources", [])
    if resources:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📚 社团推荐资源")
        lines.append("")
        for r in resources[:5]:
            title = r.get("title", "")
            covers = r.get("covers", "")
            lines.append(f"• 《{title}》" + (f" → 补充：{covers}" if covers else ""))
        lines.append("")

    # 学习建议
    suggestions = gap.get("learning_suggestions", [])
    if suggestions:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("💡 补充建议")
        lines.append("")
        for s in suggestions:
            lines.append(f"• {s}")
        lines.append("")

    # 下一步
    next_steps = gap.get("next_steps", "")
    if next_steps:
        lines.append(f"🚀 下一步：{next_steps}")

    return "\n".join(lines)