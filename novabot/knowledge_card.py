"""
NovaBot 知识卡片生成模块
聚合多篇文档生成结构化的知识卡片
"""

from typing import TYPE_CHECKING, Optional

from astrbot.api import logger

from .llm_utils import call_llm
from .prompts import CARD_PROMPT

if TYPE_CHECKING:
    from .rag import RAGEngine


class KnowledgeCardGenerator:
    """知识卡片生成器"""

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
        prompt = CARD_PROMPT.format(topic=topic, docs_content=docs_content)

        try:
            card_data = await call_llm(
                provider=provider,
                prompt=prompt,
                system_prompt="你是一个知识整理专家，善于从多篇文档中提炼核心知识。",
                require_json=True,
            )

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

        except Exception as e:
            logger.error(f"知识卡片生成失败: {e}")
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

    # 知识结构描述
    structure = card.get("structure", "")
    if structure:
        lines.append(f"📖 {structure}")
        lines.append("")

    # 核心知识
    core_knowledge = card.get("core_knowledge", [])
    if core_knowledge:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📖 核心知识")
        lines.append("")
        for k in core_knowledge:
            # 支持新格式（对象）和旧格式（字符串）
            if isinstance(k, dict):
                point = k.get("point", "")
                source = k.get("source", "")
                lines.append(f"• {point}" + (f" ({source})" if source else ""))
            else:
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

    # 个人洞见
    insights = card.get("insights", card.get("personal_insights", []))
    if insights:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("💡 个人洞见")
        lines.append("")
        for insight in insights:
            author = insight.get("author", "")
            text = insight.get("insight", "")
            source = insight.get("source", "")
            if author and text:
                lines.append(f"• {author}：{text}" + (f" ({source})" if source else ""))
        lines.append("")

    # 学习顺序
    learning_order = card.get("learning_order", [])
    if learning_order:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("📋 建议学习顺序")
        lines.append("")
        for i, step in enumerate(learning_order, 1):
            lines.append(f"{i}. {step}")
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