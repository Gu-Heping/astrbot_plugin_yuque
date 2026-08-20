"""Slash-command helpers for learning progress."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from .llm_utils import call_llm, sanitize_user_input


LEVEL_MAP = {
    "beginner": "入门",
    "intermediate": "进阶",
    "advanced": "高级",
}
VALID_LEVELS = frozenset(LEVEL_MAP)


@dataclass
class ProgressOverview:
    text: str
    progress_for_analysis: dict


def extract_progress_content(message_str: str, args: str = "") -> str:
    """Extract the full /progress argument string."""
    match = re.search(r"progress\s+(.+)$", message_str.strip(), re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return args.strip()


def build_progress_overview(memory_manager: Any, user_id: str, yuque_name: str) -> ProgressOverview:
    """Build /progress overview response."""
    progress = memory_manager.get_learning_progress(user_id)
    if not progress:
        return ProgressOverview(
            (
                f"📊 {yuque_name} 的学习进度\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"暂无学习记录\n\n"
                f"用法:\n"
                f"  /progress add <领域> <事件> - 添加里程碑\n"
                f"  /progress level <领域> <等级> - 设置等级"
            ),
            {},
        )

    lines = [f"📊 {yuque_name} 的学习进度", "━━━━━━━━━━━━━━━━━━━━"]
    lines.extend(_progress_summary_lines(progress))
    lines.append("\n🔍 正在分析学习趋势...")
    return ProgressOverview("\n".join(lines), progress)


def format_progress_overview_without_analysis(yuque_name: str, progress: dict) -> str:
    lines = [f"📊 {yuque_name} 的学习进度", "━━━━━━━━━━━━━━━━━━━━"]
    lines.extend(_progress_summary_lines(progress))
    lines.append("\n使用 /progress <领域> 查看详情")
    return "\n".join(lines)


def record_progress_milestone(memory_manager: Any, user_id: str, domain: str, event_desc: str) -> str:
    success = memory_manager.add_learning_milestone(
        user_id=user_id,
        domain=domain,
        event=event_desc,
    )
    if success:
        return f"✅ 已记录里程碑：{domain} - {event_desc}"
    return "❌ 记录失败"


def set_progress_level(memory_manager: Any, user_id: str, domain: str, level: str) -> str:
    if level not in VALID_LEVELS:
        return "等级必须是: beginner / intermediate / advanced"

    success = memory_manager.update_learning_level(user_id, domain, level)
    if success:
        return f"✅ 已设置「{domain}」等级为 {LEVEL_MAP.get(level, level)}"
    return "❌ 设置失败"


def format_domain_progress(yuque_name: str, domain: str, progress: dict) -> str:
    milestones = progress.get("milestones", [])
    level = progress.get("level", "beginner")
    next_step = progress.get("next_step")

    lines = [
        f"📊 {yuque_name} 的「{domain}」学习进度",
        "━━━━━━━━━━━━━━━━━━━━",
        f"等级: {LEVEL_MAP.get(level, level)}",
    ]

    if milestones:
        lines.append(f"\n里程碑 ({len(milestones)} 个):")
        for milestone in milestones[-10:]:
            date = milestone.get("date", "")
            event_desc = milestone.get("event", "")
            lines.append(f"• {date} - {event_desc}")
    else:
        lines.append("\n暂无里程碑记录")

    if next_step:
        lines.append(f"\n下一步建议: {next_step}")

    lines.append("\n用法: /progress add <领域> <事件>")
    return "\n".join(lines)


def progress_usage_for_add() -> str:
    return "用法: /progress add <领域> <事件>\n例如: /progress add 爬虫 完成基础教程"


def progress_usage_for_level() -> str:
    return (
        "用法: /progress level <领域> <等级>\n"
        "等级: beginner(入门) / intermediate(进阶) / advanced(高级)"
    )


async def analyze_progress_with_llm(
    *,
    provider: Any,
    user_name: str,
    progress: dict,
    token_monitor: Any = None,
) -> str:
    """Use an LLM to analyze learning progress trends."""
    safe_user_name = sanitize_user_input(user_name, max_length=50)

    domains_info = []
    for domain, data in list(progress.items())[:10]:
        safe_domain = sanitize_user_input(domain, max_length=50)
        level = LEVEL_MAP.get(data.get("level", ""), "入门")
        milestones = data.get("milestones", [])
        last_active = sanitize_user_input(data.get("last_active") or "未知", max_length=20)
        domains_info.append(
            f"- {safe_domain}: {level}，{len(milestones)} 个里程碑，最近活跃：{last_active}"
        )

    prompt = f"""你是一个学习进度分析师。请根据以下学习数据，分析用户的学习状态并给出建议。

## 用户
{safe_user_name}

## 学习进度数据
{chr(10).join(domains_info)}

## 分析任务

请输出一份简洁的分析报告，包含：

1. **学习画像总结**（2-3 句话）
   - 用户主要在学习哪些领域？
   - 整体学习进度如何？

2. **趋势分析**
   - 哪些领域学得比较好？
   - 哪些领域可能需要更多关注？

3. **下一步建议**（2-3 条）
   - 具体的学习建议
   - 可以尝试的新方向

注意：
- 语气友好，像学长/学姐在给建议
- 建议要具体，不要太笼统
- 如果用户学习领域较少，鼓励探索新领域
- 输出用中文，简洁明了"""

    try:
        return await call_llm(
            provider=provider,
            prompt=prompt,
            system_prompt="你是一个学习进度分析师，善于发现学习模式并给出实用建议。",
            require_json=False,
            token_monitor=token_monitor,
            feature="progress",
        )
    except Exception as e:
        logger.warning(f"[Progress] LLM 分析失败: {e}")
        return format_progress_overview_without_analysis(user_name, progress)


def _progress_summary_lines(progress: dict) -> list[str]:
    lines = []
    for domain_name, data in progress.items():
        level = data.get("level", "beginner")
        milestones_count = len(data.get("milestones", []))
        lines.append(
            f"• {domain_name}: {LEVEL_MAP.get(level, level)} "
            f"({milestones_count} 个里程碑)"
        )
    return lines
