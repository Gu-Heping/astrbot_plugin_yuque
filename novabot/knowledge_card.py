"""
NovaBot 知识卡片生成模块
聚合多篇文档生成结构化的知识卡片
"""

import json
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

if TYPE_CHECKING:
    from .rag import RAGEngine


class KnowledgeCardGenerator:
    """知识卡片生成器"""

    CARD_PROMPT = """你是一个知识整理助手。请根据以下文档内容，生成一份结构化的知识卡片。

## 主题
{topic}

## 相关文档
{docs_content}

## 输出要求

请生成一份知识卡片，包含以下内容：

1. **核心知识**：提取该主题的核心知识点（3-5 条），简洁明了
2. **学习路径**：建议的学习顺序（如果适用）
3. **工具/资源**：涉及的工具、库、框架等
4. **个人思考**：从文档中提取作者的个人见解、踩坑经验、建议等

## 输出格式

请严格按以下 JSON 格式输出，不要有多余内容：

```json
{{
  "topic": "主题名称",
  "core_knowledge": [
    "知识点1",
    "知识点2",
    "知识点3"
  ],
  "tools": ["工具1", "工具2"],
  "personal_insights": [
    {{"author": "作者", "insight": "个人见解"}},
    {{"author": "作者", "insight": "踩坑经验"}}
  ],
  "summary": "一句话总结"
}}
```

注意：
- 核心知识要简洁，每条不超过 50 字
- 个人思考要保留原作者信息
- 如果文档中没有个人思考，personal_insights 可以是空数组"""

    def __init__(self, rag: "RAGEngine"):
        """初始化生成器

        Args:
            rag: RAG 引擎实例
        """
        self.rag = rag

    def build_docs_content(self, docs: list, max_docs: int = 5) -> str:
        """构建文档内容字符串

        Args:
            docs: 文档列表
            max_docs: 最大文档数

        Returns:
            格式化的文档内容
        """
        lines = []
        for i, doc in enumerate(docs[:max_docs], 1):
            title = doc.get("title", "无标题")
            author = doc.get("author", "未知")
            book = doc.get("book_name", "")
            content = doc.get("content", "")[:800]  # 限制内容长度

            lines.append(f"### 文档 {i}: {title}")
            lines.append(f"作者: {author}")
            if book:
                lines.append(f"知识库: {book}")
            lines.append(f"内容:\n{content}...")
            lines.append("")

        return "\n".join(lines)

    async def generate(
        self,
        topic: str,
        provider,
        max_docs: int = 5
    ) -> dict:
        """生成知识卡片

        Args:
            topic: 主题
            provider: LLM Provider
            max_docs: 使用的最大文档数

        Returns:
            知识卡片字典
        """
        if not self.rag:
            return {"error": "RAG 引擎未初始化"}

        # 1. 检索相关文档
        try:
            docs = self.rag.search(topic, k=max_docs * 2)  # 多取一些用于筛选
            if not docs:
                return {
                    "topic": topic,
                    "error": f"未找到与「{topic}」相关的文档"
                }
        except Exception as e:
            logger.error(f"RAG 检索失败: {e}")
            return {"topic": topic, "error": f"检索失败: {e}"}

        # 2. 构建文档内容
        docs_content = self.build_docs_content(docs[:max_docs], max_docs)

        # 3. 调用 LLM 生成卡片
        prompt = self.CARD_PROMPT.format(topic=topic, docs_content=docs_content)

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                context=[],
                system_prompt="你是一个知识整理助手，输出格式必须是 JSON。"
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

            card_data = json.loads(result_text)

            # 4. 添加来源文档信息
            card_data["source_docs"] = [
                {
                    "title": doc.get("title", ""),
                    "author": doc.get("author", ""),
                    "book_name": doc.get("book_name", ""),
                }
                for doc in docs[:max_docs]
            ]

            return card_data

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return {"topic": topic, "error": "生成结果解析失败"}
        except Exception as e:
            logger.error(f"LLM 生成失败: {e}")
            return {"topic": topic, "error": f"生成失败: {e}"}


def format_knowledge_card(card: dict) -> str:
    """格式化知识卡片为文本

    Args:
        card: 知识卡片字典

    Returns:
        格式化的文本
    """
    if card.get("error"):
        return f"❌ {card['error']}"

    lines = [f"📚 知识卡片：{card.get('topic', '未知主题')}"]
    lines.append("")

    # 核心知识
    core_knowledge = card.get("core_knowledge", [])
    if core_knowledge:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📖 核心知识")
        lines.append("")
        for k in core_knowledge:
            lines.append(f"• {k}")
        lines.append("")

    # 工具/资源
    tools = card.get("tools", [])
    if tools:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("🔧 工具/资源")
        lines.append("")
        lines.append(f"• {', '.join(tools)}")
        lines.append("")

    # 个人思考
    insights = card.get("personal_insights", [])
    if insights:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("💡 个人思考")
        lines.append("")
        for insight in insights:
            author = insight.get("author", "")
            text = insight.get("insight", "")
            if author and text:
                lines.append(f"• {author}：{text}")
        lines.append("")

    # 来源文档
    sources = card.get("source_docs", [])
    if sources:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"📄 来源文档（{len(sources)} 篇）")
        lines.append("")
        for src in sources[:5]:
            title = src.get("title", "")
            author = src.get("author", "")
            lines.append(f"• 《{title}》- {author}")
        lines.append("")

    # 总结
    summary = card.get("summary", "")
    if summary:
        lines.append(f"📝 {summary}")

    return "\n".join(lines)