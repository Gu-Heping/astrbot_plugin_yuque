"""Slash-command helpers for collaboration network queries."""

from __future__ import annotations

import re
from typing import Any

from .llm_utils import sanitize_user_input


def extract_collab_content(message_str: str, args: str = "") -> str:
    """Extract the full /collab argument string."""
    match = re.search(r"collab\s+(.+)$", message_str.strip(), re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return args.strip()


def build_collab_find_query(topic: str) -> str:
    safe_topic = sanitize_user_input(topic, max_length=100)
    return f"我想找一个在「{safe_topic}」领域有经验的协作伙伴，能帮我一起做项目或请教问题"


def collab_usage_for_find() -> str:
    return "用法: /collab find <主题>"


def format_potential_collaborators(
    topic: str,
    potential: list[dict],
    *,
    members: dict,
    doc_index: Any = None,
    team_id: str | None = None,
) -> str:
    """Format fallback potential collaborator recommendations."""
    lines = [f"【「{topic}」领域潜在协作伙伴】\n"]

    for candidate in potential[:5]:
        partner_id = str(candidate.get("member_id", ""))
        member_info = _member_info(members, partner_id)
        partner_name = (
            member_info.get("name") or member_info.get("login") or partner_id
            if member_info
            else partner_id
        )
        partner_login = member_info.get("login", "") if member_info else ""
        score = candidate.get("match_score", 0)
        reasons = candidate.get("match_reasons", [])

        lines.append(f"👤 {partner_name}（匹配度 {score:.0%}）")
        for reason in reasons:
            lines.append(f"   ✓ {reason}")

        partner_doc = _find_partner_doc(
            topic,
            partner_id,
            partner_name,
            doc_index,
            team_id=team_id,
        )
        if partner_doc:
            lines.append(f"   📄 相关文档：{partner_doc[:25]}...")

        if partner_login:
            lines.append(f"   🔗 https://www.yuque.com/{partner_login}")

        lines.append("")

    lines.append("💡 提示：可以在群里 @对方 讨论，或通过语雀主页私信联系")
    return "\n".join(lines)


def format_collaborators(
    target_name: str,
    collaborators: list[dict],
    *,
    stats: dict,
    members: dict,
) -> str:
    if not collaborators:
        return f"「{target_name}」暂无协作记录"

    lines = [f"【{target_name} 的协作伙伴】"]
    for collab in collaborators[:10]:
        partner_id = str(collab.get("member_id", ""))
        member_info = _member_info(members, partner_id)
        partner_name = (
            member_info.get("name") or member_info.get("login") or partner_id
            if member_info
            else partner_id
        )
        strength = collab.get("strength", 0)
        source_name = collab.get("source_name", "")
        context = collab.get("context", "")

        line = f"• {partner_name}（强度 {strength:.0%}，{source_name}"
        if context:
            line += f"：{context}"
        line += "）"
        lines.append(line)

    lines.append(f"\n统计：{stats.get('collaborator_count', 0)} 位协作伙伴")
    return "\n".join(lines)


def _find_partner_doc(
    topic: str,
    partner_id: str,
    partner_name: str,
    doc_index: Any,
    *,
    team_id: str | None = None,
) -> str:
    if not doc_index:
        return ""
    try:
        docs = doc_index.search(title=topic, team_id=team_id, limit=5)
    except TypeError:
        docs = doc_index.search(title=topic, limit=5)
    except Exception:
        return ""
    for doc in docs:
        doc_author = str(doc.get("creator_id") or doc.get("author", ""))
        if doc_author == partner_id or doc.get("author") == partner_name:
            return doc.get("title", "")
    return ""


def _member_info(members: dict, member_id: str) -> dict:
    info = members.get(member_id)
    if info:
        return info
    if member_id.isdigit():
        return members.get(int(member_id), {})
    return {}
