"""
NovaBot 用户画像生成模块
基于 LLM 分析用户文档，生成技术画像
"""

import json

from astrbot.api import logger


class ProfileGenerator:
    """用户画像生成器（LLM 驱动）"""

    PROFILE_PROMPT = """你是一个专业的技术能力分析助手。请根据用户的文档信息，生成一份简洁的用户画像。

## 分析维度

1. **技术领域**：识别用户涉足的技术领域（不限于此列表，自由发现）
2. **认知水平**：评估用户在各领域的理解深度
   - beginner：刚开始接触，学习基础概念
   - intermediate：能独立完成项目，理解原理
   - advanced：深入底层，能优化和创新
3. **特点标签**：用户的学习风格、产出特点

## 用户文档信息

{docs_info}

## 输出格式

请严格按以下 JSON 格式输出，不要有多余内容：

```json
{{
  "interests": ["领域1", "领域2", "领域3"],
  "skills": {{
    "领域1": "intermediate",
    "领域2": "beginner",
    "领域3": "advanced"
  }},
  "level": "intermediate",
  "tags": ["标签1", "标签2"],
  "summary": "一句话概括这个用户的技术特点"
}}
```

注意：
- interests 最多 5 个领域
- skills 和 level 的值必须用英文：beginner / intermediate / advanced
- tags 最多 3 个标签
- 所有字段必须有值"""

    def build_docs_info(self, docs: list) -> str:
        """构建文档信息字符串"""
        if not docs:
            return "暂无文档"

        lines = []
        for i, doc in enumerate(docs[:30], 1):  # 最多30篇
            title = doc.get("title", "无标题")
            book = doc.get("book_name", "未知知识库")
            content = doc.get("content", "")[:200] if doc.get("content") else ""
            lines.append(f"{i}. [{book}] {title}")
            if content:
                lines.append(f"   摘要: {content[:100]}...")

        return "\n".join(lines)

    def _normalize_level(self, level: str) -> str:
        """标准化水平值（支持中英文）"""
        mapping = {
            "beginner": "beginner", "入门": "beginner", "初级": "beginner",
            "intermediate": "intermediate", "进阶": "intermediate", "中级": "intermediate",
            "advanced": "advanced", "高级": "advanced", "高级": "advanced",
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
        prompt = self.PROFILE_PROMPT.format(docs_info=docs_info)

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                context=[],
                system_prompt="你是一个专业的技术能力分析助手，输出格式必须是 JSON。"
            )

            result_text = resp.completion_text.strip()

            # 提取 JSON
            # 尝试从 markdown 代码块中提取
            if "```json" in result_text:
                start = result_text.find("```json") + 7
                end = result_text.find("```", start)
                result_text = result_text[start:end].strip()
            elif "```" in result_text:
                start = result_text.find("```") + 3
                end = result_text.find("```", start)
                result_text = result_text[start:end].strip()

            profile_data = json.loads(result_text)

            # 标准化水平值
            normalized_skills = {
                k: self._normalize_level(v)
                for k, v in profile_data.get("skills", {}).items()
            }

            # 构建返回格式
            return {
                "profile": {
                    "interests": profile_data.get("interests", []),
                    "level": self._normalize_level(profile_data.get("level", "beginner")),
                    "skills": normalized_skills,
                    "tags": profile_data.get("tags", []),
                    "summary": profile_data.get("summary", ""),
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
            "profile": {"interests": [], "level": "beginner", "skills": {}, "tags": [], "summary": ""},
            "stats": {"docs_count": 0, "repos": []}
        }

    # ========== 领域认知评估 ==========

    DOMAIN_ASSESS_PROMPT = """你是一个技术能力评估专家。请分析用户在「{domain}」领域的掌握程度。

## 用户在该领域的文档

{domain_docs}

## 评估标准

1. **已掌握的知识点**：用户已经理解并能应用的内容
2. **正在学习中**：用户正在探索但尚未完全掌握的内容
3. **建议下一步学习**：基于用户当前水平，推荐的学习方向

## 输出格式

请严格按以下 JSON 格式输出：

```json
{{
  "current_level": "beginner/intermediate/advanced",
  "mastered": ["知识点1", "知识点2"],
  "learning": ["正在学的内容"],
  "next_steps": ["建议学习的内容"],
  "assessment": "简短的评价（50字以内）"
}}
```

注意：
- current_level 必须是 beginner / intermediate / advanced 之一
- mastered 和 learning 各最多 5 条
- next_steps 最多 3 条"""

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
        provider
    ) -> dict:
        """评估用户在特定领域的水平

        Args:
            docs: 用户所有文档
            domain: 要评估的领域
            provider: LLM Provider

        Returns:
            领域评估结果
        """
        # 筛选相关文档
        domain_docs = self.filter_docs_by_domain(docs, domain)

        if not domain_docs:
            return {
                "domain": domain,
                "current_level": "未接触",
                "mastered": [],
                "learning": [],
                "next_steps": [f"建议先了解 {domain} 的基础知识"],
                "assessment": f"未找到与「{domain}」相关的文档"
            }

        # 构建文档信息
        docs_info = self.build_docs_info(domain_docs[:10])

        # 调用 LLM 评估
        prompt = self.DOMAIN_ASSESS_PROMPT.format(domain=domain, domain_docs=docs_info)

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                context=[],
                system_prompt="你是一个技术能力评估专家，输出格式必须是 JSON。"
            )

            result_text = resp.completion_text.strip()

            # 提取 JSON
            if "```json" in result_text:
                start = result_text.find("```json") + 7
                end = result_text.find("```", start)
                result_text = result_text[start:end].strip()
            elif "```" in result_text:
                start = result_text.find("```") + 3
                end = result_text.find("```", start)
                result_text = result_text[start:end].strip()

            result = json.loads(result_text)

            # 标准化水平值
            result["current_level"] = self._normalize_level(result.get("current_level", "beginner"))
            result["domain"] = domain
            result["docs_count"] = len(domain_docs)

            return result

        except Exception as e:
            logger.error(f"领域评估失败: {e}")
            return {
                "domain": domain,
                "current_level": "未知",
                "mastered": [],
                "learning": [],
                "next_steps": [],
                "assessment": f"评估失败: {e}"
            }


def format_domain_assessment(assessment: dict) -> str:
    """格式化领域评估结果

    Args:
        assessment: 评估结果字典

    Returns:
        格式化的文本
    """
    domain = assessment.get("domain", "未知领域")
    level = assessment.get("current_level", "未知")

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

    # 下一步建议
    next_steps = assessment.get("next_steps", [])
    if next_steps:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("🎯 建议下一步")
        for n in next_steps:
            lines.append(f"• {n}")
        lines.append("")

    # 评价
    assessment_text = assessment.get("assessment", "")
    if assessment_text:
        lines.append(f"📝 {assessment_text}")

    return "\n".join(lines)