"""
NovaBot 学习路径推荐模块
根据用户水平和目标领域生成学习计划
"""

import json
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

if TYPE_CHECKING:
    from .rag import RAGEngine
    from .storage import Storage


class LearningPathRecommender:
    """学习路径推荐器"""

    PATH_PROMPT = """你是一个学习路径规划专家。请根据用户的情况和社团内的资源，生成一份学习路径。

## 用户信息

- 当前水平：{current_level}
- 已掌握技能：{mastered_skills}
- 目标领域：{target_domain}

## 社团内相关资源

{resources}

## 输出要求

请生成一份分阶段的学习路径：

1. **阶段划分**：根据用户当前水平，划分 2-3 个学习阶段
2. **每个阶段包含**：
   - 学习目标
   - 推荐文档/资源（从上述资源中选择）
   - 预计时间
   - 关键任务
3. **学习伙伴推荐**：可以一起学习的同学或导师

## 输出格式

请严格按以下 JSON 格式输出：

```json
{{
  "target_domain": "目标领域",
  "total_stages": 2,
  "stages": [
    {{
      "stage": 1,
      "title": "阶段名称",
      "goals": ["目标1", "目标2"],
      "resources": ["推荐文档标题"],
      "duration": "1-2周",
      "tasks": ["任务1", "任务2"]
    }}
  ],
  "partners": [
    {{
      "type": "study_buddy/mentor",
      "reason": "推荐理由"
    }}
  ],
  "tips": "学习建议"
}}
```

注意：
- stages 最多 3 个阶段
- 每个阶段 goals 最多 3 个
- resources 必须从提供的资源列表中选择"""

    def __init__(self, storage: "Storage", rag: Optional["RAGEngine"] = None):
        """初始化推荐器

        Args:
            storage: Storage 实例
            rag: RAG 引擎（可选）
        """
        self.storage = storage
        self.rag = rag

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

    def build_resources_text(self, resources: list) -> str:
        """构建资源文本

        Args:
            resources: 资源列表

        Returns:
            格式化的文本
        """
        if not resources:
            return "暂无相关资源"

        lines = []
        for i, r in enumerate(resources[:10], 1):
            title = r.get("title", "")
            author = r.get("author", "")
            lines.append(f"{i}. 《{title}》" + (f" - {author}" if author else ""))

        return "\n".join(lines)

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
        resources_text = self.build_resources_text(resources)

        # 构建提示词
        prompt = self.PATH_PROMPT.format(
            current_level=current_level,
            mastered_skills=", ".join(mastered_skills) if mastered_skills else "暂无",
            target_domain=target_domain,
            resources=resources_text
        )

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                context=[],
                system_prompt="你是一个学习路径规划专家，输出格式必须是 JSON。"
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

    # 各阶段
    stages = path.get("stages", [])
    if stages:
        for stage in stages:
            stage_num = stage.get("stage", 1)
            title = stage.get("title", f"阶段 {stage_num}")
            duration = stage.get("duration", "")
            goals = stage.get("goals", [])
            tasks = stage.get("tasks", [])
            resources = stage.get("resources", [])

            lines.append(f"━━━━━━━━━━━━━━━")
            lines.append(f"📚 阶段 {stage_num}：{title}" + (f"（{duration}）" if duration else ""))
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

            if tasks:
                lines.append("✅ 关键任务")
                for t in tasks:
                    lines.append(f"• {t}")
                lines.append("")

    # 学习伙伴
    partners = path.get("partners", [])
    if partners:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("👥 学习伙伴推荐")
        lines.append("")
        for p in partners:
            ptype = p.get("type", "")
            reason = p.get("reason", "")
            ptype_text = "学习伙伴" if ptype == "study_buddy" else "导师" if ptype == "mentor" else ptype
            lines.append(f"• {ptype_text}：{reason}")
        lines.append("")

    # 建议
    tips = path.get("tips", "")
    if tips:
        lines.append(f"💡 {tips}")

    return "\n".join(lines)