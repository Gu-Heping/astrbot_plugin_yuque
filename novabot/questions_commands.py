"""Slash-command helpers for question archives."""

from __future__ import annotations

import re
from typing import Any

from astrbot.api import logger


def extract_questions_content(message_str: str, args: str = "") -> str:
    """Extract the full /questions argument string."""
    match = re.search(r"questions\s+(.+)$", message_str.strip(), re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return args.strip()


def format_unresolved_questions(yuque_name: str, questions: list[dict]) -> str:
    if not questions:
        return (
            f"🎉 {yuque_name} 没有未解决的问题！\n\n"
            f"用法:\n"
            f"  /questions all - 查看所有问题\n"
            f"  /questions frequent - 反复出现的问题"
        )

    lines = [
        f"❓ {yuque_name} 的未解决问题 ({len(questions)} 个)",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for question in questions:
        question_text = question.get("question", "")
        ask_count = question.get("ask_count", 1)
        qid = question.get("question_id", "")
        lines.append(f"• [{qid}] {question_text}")
        if ask_count > 1:
            lines.append(f"  (问过 {ask_count} 次)")

    lines.append("\n使用 /questions resolve <ID> 标记已解决")
    return "\n".join(lines)


def format_all_questions(yuque_name: str, questions: list[dict], stats: dict) -> str:
    if not questions:
        return f"{yuque_name} 暂无问题记录"

    lines = [
        f"📋 {yuque_name} 的问题档案",
        "━━━━━━━━━━━━━━━━━━━━",
        f"总计: {stats['total']} | 已解决: {stats['resolved']} | 未解决: {stats['unresolved']}",
        "",
    ]

    for question in questions[:20]:
        question_text = question.get("question", "")
        ask_count = question.get("ask_count", 1)
        resolved = question.get("resolved", False)
        qid = question.get("question_id", "")
        status = "✅" if resolved else "❓"
        count_str = f" (×{ask_count})" if ask_count > 1 else ""
        lines.append(f"{status} [{qid}] {question_text}{count_str}")

    if len(questions) > 20:
        lines.append(f"\n... 还有 {len(questions) - 20} 个问题")

    return "\n".join(lines)


def format_frequent_questions(
    yuque_name: str,
    questions: list[dict],
    related_docs: list[dict] | None = None,
) -> str:
    if not questions:
        return f"{yuque_name} 没有反复出现的问题"

    sorted_questions = sorted(questions, key=lambda q: q.get("ask_count", 1), reverse=True)
    lines = [
        f"🔄 {yuque_name} 反复出现的问题 ({len(sorted_questions)} 个)",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for question in sorted_questions:
        question_text = question.get("question", "")
        ask_count = question.get("ask_count", 1)
        resolved = question.get("resolved", False)
        qid = question.get("question_id", "")
        status = "✅" if resolved else "❓"
        lines.append(f"{status} [{qid}] {question_text} (问过 {ask_count} 次)")

    lines.extend(
        [
            "",
            "💡 建议：",
            "• 这些反复出现的问题可能需要找导师帮忙",
            "• 可以用 /partner 找相关领域的学习伙伴",
            "• 或在群里 @相关成员 讨论",
        ]
    )

    if related_docs:
        lines.append("\n📚 相关文档：")
        for doc in related_docs:
            title = doc.get("title", "")
            author = doc.get("author", "")
            lines.append(f"• 《{title}》- {author}")

    return "\n".join(lines)


def find_related_docs_for_questions(doc_index: Any, questions: list[dict], limit: int = 3) -> list[dict]:
    """Find likely related docs for frequent questions."""
    if not doc_index or not questions:
        return []

    try:
        keywords = extract_question_keywords(questions)
        if not keywords:
            return []
        return doc_index.search(title=" ".join(keywords[:3]), limit=limit)
    except Exception as e:
        logger.debug(f"[Questions] 相关文档推荐失败: {e}")
        return []


def extract_question_keywords(questions: list[dict]) -> list[str]:
    keywords: set[str] = set()
    for question in questions[:5]:
        for keyword in re.findall(r"[\u4e00-\u9fa5]+|[a-zA-Z]+", question.get("question", "")):
            if len(keyword) >= 2:
                keywords.add(keyword)
    return sorted(keywords)


def parse_resolve_args(rest: str) -> tuple[str, str]:
    parts = rest.strip().split(maxsplit=1)
    qid = parts[0] if parts else ""
    resolution = parts[1] if len(parts) > 1 else ""
    return qid, resolution


def format_resolve_question_result(qid: str, success: bool) -> str:
    if success:
        return f"✅ 问题 {qid} 已标记为已解决"
    return f"❌ 未找到问题 {qid}"


def questions_usage_for_resolve() -> str:
    return "用法: /questions resolve <ID>\n例如: /questions resolve q_abc123"


def format_unknown_questions_action(action: str) -> str:
    return (
        f"未知操作: {action}\n"
        f"用法:\n"
        f"  /questions - 未解决的问题\n"
        f"  /questions all - 所有问题\n"
        f"  /questions frequent - 反复出现的问题\n"
        f"  /questions resolve <ID> - 标记已解决"
    )
