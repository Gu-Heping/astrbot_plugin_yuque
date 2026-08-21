"""Slash-command helpers for conversation memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from astrbot.api import logger

from .llm_utils import call_llm, sanitize_user_input


@dataclass
class BoundMemoryUser:
    user_id: str
    yuque_name: str


@dataclass
class MemoryOverview:
    text: str
    sessions_for_analysis: list[dict]


def resolve_bound_memory_user(storage: Any, platform_id: str) -> tuple[BoundMemoryUser | None, str | None]:
    """Resolve a platform sender into a memory user."""
    binding = storage.get_binding(platform_id)
    if not binding:
        return None, "请先绑定账号：/bind <用户名>"

    yuque_id = binding.get("yuque_id")
    if not yuque_id:
        return None, "绑定信息异常，请重新绑定"

    return BoundMemoryUser(
        user_id=str(yuque_id),
        yuque_name=binding.get("yuque_name", "未知"),
    ), None


def build_memory_overview(memory_manager: Any, user_id: str, yuque_name: str) -> MemoryOverview:
    """Build the default /memory overview response."""
    stats = memory_manager.get_user_stats(user_id)
    total_sessions = stats.get("total_sessions", 0)
    total_messages = stats.get("total_messages", 0)
    recent_7_days = stats.get("recent_7_days", 0)

    lines = [
        f"🧠 {yuque_name} 的记忆概览",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• 总会话数: {total_sessions}",
        f"• 总消息数: {total_messages}",
        f"• 近7天活跃: {recent_7_days} 次",
    ]

    sessions_for_analysis: list[dict] = []
    if total_sessions >= 3:
        sessions_for_analysis = memory_manager.get_recent_sessions(user_id, limit=5)
        if sessions_for_analysis:
            lines.append("\n🔍 正在分析对话模式...")
            return MemoryOverview("\n".join(lines), sessions_for_analysis)

    lines.extend(memory_usage_lines())
    return MemoryOverview("\n".join(lines), [])


def format_recent_memory(yuque_name: str, sessions: list[dict]) -> str:
    """Format recent conversation sessions."""
    if not sessions:
        return "暂无对话历史"

    lines = [f"📋 {yuque_name} 的最近对话", "━━━━━━━━━━━━━━━━━━━━"]
    for session in sessions:
        lines.append(f"• [{_format_memory_date(session.get('started_at', ''))}] {session.get('summary', '无摘要')}")

    lines.append(f"\n共 {len(sessions)} 条对话记录")
    lines.append("💡 可以直接问我「上次我们聊了什么」")
    return "\n".join(lines)


def extract_memory_search_keyword(message_str: str, keyword: str = "") -> str:
    """Extract the full keyword from /memory search."""
    match = re.search(r"memory\s+search\s+(.+)$", message_str.strip(), re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return keyword.strip()


def format_memory_search_results(search_keyword: str, results: list[dict]) -> str:
    """Format /memory search results."""
    if not results:
        return f"未找到包含「{search_keyword}」的对话"

    lines = [f"🔍 搜索「{search_keyword}」的结果", "━━━━━━━━━━━━━━━━━━━━"]
    for result in results:
        lines.append(f"• [{_format_memory_date(result.get('started_at', ''))}] {result.get('summary', '')}")

    lines.append(f"\n找到 {len(results)} 条相关对话")
    return "\n".join(lines)


def format_memory_clear_result(yuque_name: str, success: bool) -> str:
    if success:
        return f"✅ 已清除 {yuque_name} 的记忆"
    return "❌ 清除失败，请稍后重试"


def format_unknown_memory_action(action: str) -> str:
    return (
        f"未知操作: {action}\n"
        f"用法:\n"
        f"  /memory - 概览\n"
        f"  /memory recent - 最近对话\n"
        f"  /memory search <关键词> - 搜索\n"
        f"  /memory clear - 清除"
    )


def memory_usage_lines() -> list[str]:
    return [
        "\n指令:",
        "  /memory recent - 最近对话",
        "  /memory search <关键词> - 搜索",
        "  /memory clear - 清除记忆",
    ]


async def analyze_memory_with_llm(
    *,
    provider: Any,
    user_name: str,
    sessions: list[dict],
    token_monitor: Any = None,
) -> str:
    """Use an LLM to summarize a user's conversation patterns."""
    safe_user_name = sanitize_user_input(user_name, max_length=50)

    sessions_info = []
    for session in sessions[:5]:
        summary = sanitize_user_input(session.get("summary", "无摘要"), max_length=100)
        started_at = session.get("started_at", "")[:10] if session.get("started_at") else ""
        sessions_info.append(f"- [{started_at}] {summary}")

    prompt = f"""你是一个对话分析师。请根据用户的对话历史，分析他们的学习模式和兴趣。

## 用户
{safe_user_name}

## 最近对话记录（共 {len(sessions)} 条）
{chr(10).join(sessions_info)}

## 分析任务

请输出一份简洁的分析，包含：

1. **对话画像**（1-2 句话）
   - 用户主要关心什么？
   - 提问频率如何？

2. **兴趣领域**
   - 从对话摘要中识别用户关注的技术/知识领域
   - 列出 2-3 个主要领域

3. **建议**（1-2 条）
   - 基于对话内容，给出学习或探索建议

注意：
- 语气友好
- 输出用中文，简洁明了"""

    try:
        return await call_llm(
            provider=provider,
            prompt=prompt,
            system_prompt="你是一个对话分析师，善于从对话记录中发现用户兴趣。",
            require_json=False,
            token_monitor=token_monitor,
            feature="memory",
        )
    except Exception as e:
        logger.warning(f"[Memory] LLM 分析失败: {e}")
        return (
            f"🧠 {safe_user_name} 的记忆概览\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• 总会话数: {len(sessions)}\n\n"
            f"指令:\n"
            f"  /memory recent - 最近对话\n"
            f"  /memory search <关键词> - 搜索\n"
            f"  /memory clear - 清除记忆"
        )


def _format_memory_date(started_at: str) -> str:
    if not started_at:
        return "未知日期"
    try:
        return datetime.fromisoformat(started_at).strftime("%m-%d %H:%M")
    except ValueError:
        return started_at[:10]
