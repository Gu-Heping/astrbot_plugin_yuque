"""Slash-command helpers for member trajectories."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .llm_utils import call_llm, sanitize_user_input


def extract_trajectory_content(message_str: str, args: str = "") -> str:
    """Extract the full /trajectory argument string."""
    match = re.search(r"trajectory\s+(.+)$", message_str.strip(), re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return args.strip()


def build_trajectory_topic_query(topic: str) -> str:
    safe_topic = sanitize_user_input(topic, max_length=100)
    return f"查看最近谁在做与「{safe_topic}」相关的事情"


def format_topic_fallback(topic: str, results: list[dict], members: dict) -> str:
    if not results:
        return f"最近 30 天没有成员在做「{topic}」相关的事情"

    lines = [f"【与「{topic}」相关的成员活动】\n"]
    for result in results[:5]:
        member_id = str(result.get("member_id", ""))
        member_info = _member_info(members, member_id)
        member_name = (
            member_info.get("name") or member_info.get("login") or member_id
            if member_info
            else member_id
        )
        match_count = result.get("match_count", 0)
        events = result.get("matching_events", [])[:3]
        stats = result.get("stats", {})

        lines.append(f"👤 {member_name}（{match_count} 次相关活动）")
        for event in events:
            event_name = event.get("event_name", "")
            title = event.get("title", "")
            lines.append(f"   • {event_name}：{title[:25]}...")

        doc_count = stats.get("doc_count", 0)
        if doc_count:
            lines.append(f"   📄 共 {doc_count} 篇相关文档")

        lines.append("")

    lines.append("💡 提示：可以主动联系他们请教问题")
    return "\n".join(lines)


def find_member_id_by_name(members: dict, name_or_login: str) -> str:
    lookup = name_or_login.strip()
    lookup_lower = lookup.lower()
    for uid, info in members.items():
        name = str(info.get("name", ""))
        login = str(info.get("login", ""))
        if lookup in (name, login) or lookup_lower in (name.lower(), login.lower()):
            return str(uid)
    return ""


def format_member_trajectory(target_name: str, trajectory: list[dict]) -> str:
    if not trajectory:
        return f"「{target_name}」最近 30 天暂无活动记录"

    lines = [f"【{target_name} 最近活动】"]
    for event in trajectory[:10]:
        event_name = event.get("event_name", "活动")
        title = event.get("title", "")
        lines.append(f"• {_format_trajectory_date(event.get('timestamp', ''))} - {event_name}：{title[:30]}")

    if len(trajectory) > 10:
        lines.append(f"\n... 还有 {len(trajectory) - 10} 条记录")

    return "\n".join(lines)


def should_analyze_trajectory(is_self: bool, trajectory: list[dict]) -> bool:
    return is_self and len(trajectory) >= 3


def trajectory_usage_for_topic() -> str:
    return "用法: /trajectory topic <主题>"


async def analyze_trajectory_with_llm(
    *,
    provider: Any,
    user_name: str,
    trajectory: list[dict],
    token_monitor: Any = None,
) -> str:
    """Use an LLM to analyze member trajectory patterns."""
    safe_user_name = sanitize_user_input(user_name, max_length=50)

    events_info = []
    event_types: dict[str, int] = {}
    for event in trajectory[:15]:
        event_name = sanitize_user_input(event.get("event_name", "活动"), max_length=30)
        title = sanitize_user_input(event.get("title", ""), max_length=50)
        timestamp = event.get("timestamp", "")[:10] if event.get("timestamp") else ""
        events_info.append(f"- [{timestamp}] {event_name}：{title[:40]}")
        event_types[event_name] = event_types.get(event_name, 0) + 1

    type_summary = ", ".join(f"{key}×{value}" for key, value in list(event_types.items())[:5])

    prompt = f"""你是一个学习活动分析师。请根据用户的活动轨迹，分析他们的学习状态。

## 用户
{safe_user_name}

## 最近活动记录（共 {len(trajectory)} 条）
{chr(10).join(events_info)}

## 活动类型统计
{type_summary}

## 分析任务

请输出一份简洁的活动分析，包含：

1. **活动画像**（2-3 句话）
   - 用户主要在做什么类型的事情？
   - 活动频率如何？

2. **兴趣领域**
   - 从活动标题中识别用户关注的技术/知识领域
   - 列出 2-3 个主要领域

3. **学习建议**（1-2 条）
   - 基于活动模式，给出下一步学习建议
   - 或建议探索的新方向

注意：
- 语气友好，像学长/学姐在分析
- 从活动标题中推断技术领域时要合理
- 如果活动较少，鼓励用户多记录学习过程
- 输出用中文，简洁明了"""

    try:
        return await call_llm(
            provider=provider,
            prompt=prompt,
            system_prompt="你是一个学习活动分析师，善于从活动记录中发现学习模式。",
            require_json=False,
            token_monitor=token_monitor,
            feature="trajectory",
        )
    except Exception as e:
        logger.warning(f"[Trajectory] LLM 分析失败: {e}")
        return format_member_trajectory(user_name, trajectory)


def _member_info(members: dict, member_id: str) -> dict:
    info = members.get(member_id)
    if info:
        return info
    if member_id.isdigit():
        return members.get(int(member_id), {})
    return {}


def _format_trajectory_date(timestamp: str) -> str:
    if not timestamp:
        return "未知"
    try:
        return datetime.fromisoformat(timestamp).strftime("%m-%d")
    except ValueError:
        return timestamp[:10]
