"""Slash-command helpers for partner recommendations."""

from __future__ import annotations

from typing import Any

from .llm_utils import sanitize_user_input
from .partner import format_partner_result


def partner_missing_profile_message() -> str:
    return "⚠️ 你还没有画像\n使用 /profile refresh 生成画像后再来找我推荐伙伴"


def build_partner_agent_query(topic: str = "") -> str:
    safe_topic = sanitize_user_input(topic, max_length=100) if topic else ""
    if safe_topic:
        return f"我想找一个在「{safe_topic}」领域的学习伙伴或导师"
    return "请根据我的兴趣推荐学习伙伴或导师"


def find_partner_fallback(
    *,
    matcher: Any,
    storage: Any,
    yuque_id: int,
    topic: str = "",
) -> str:
    scoped_topic = topic if topic else None
    partners = matcher.find_partners(yuque_id, scoped_topic)
    mentors = matcher.find_mentors(yuque_id, scoped_topic)
    if partners or mentors:
        return format_partner_result(partners, mentors, scoped_topic, storage=storage)
    if topic:
        return f"未找到「{topic}」相关的学习伙伴"
    return "暂无匹配的学习伙伴"
