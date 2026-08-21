"""Command helpers for knowledge card generation."""

from __future__ import annotations

from typing import Protocol

from .knowledge_card import KnowledgeCardGenerator, format_knowledge_card


class CardGeneratorLike(Protocol):
    async def generate(self, topic: str, provider) -> dict: ...


def validate_card_request(topic: str, provider, rag) -> str:
    """Return an error message when /card cannot run."""
    if not topic:
        return "用法: /card <主题>\n例如: /card 爬虫"
    if not provider:
        return "❌ LLM 未配置，无法生成知识卡片"
    if not rag:
        return "❌ RAG 引擎未初始化，请先执行 /sync"
    return ""


async def generate_card_command(
    *,
    topic: str,
    provider,
    rag,
    token_monitor=None,
    generator: CardGeneratorLike | None = None,
) -> str:
    """Generate and format a knowledge card for the /card command."""
    error = validate_card_request(topic, provider, rag)
    if error:
        return error

    card_generator = generator or KnowledgeCardGenerator(rag, token_monitor)
    card = await card_generator.generate(topic, provider)
    return format_knowledge_card(card)
