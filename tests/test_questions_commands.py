from __future__ import annotations

from novabot.questions_commands import (
    extract_question_keywords,
    extract_questions_content,
    find_related_docs_for_questions,
    format_all_questions,
    format_frequent_questions,
    format_resolve_question_result,
    format_unknown_questions_action,
    format_unresolved_questions,
    parse_resolve_args,
    questions_usage_for_resolve,
)


def _questions():
    return [
        {
            "question_id": "q_1",
            "question": "Python 爬虫 怎么处理登录",
            "ask_count": 3,
            "resolved": False,
        },
        {
            "question_id": "q_2",
            "question": "向量检索 chunk 怎么调",
            "ask_count": 2,
            "resolved": True,
        },
    ]


class _DocIndex:
    def __init__(self):
        self.query = None

    def search(self, title, limit):
        self.query = (title, limit)
        return [{"title": "爬虫登录指南", "author": "Alice"}]


def test_extract_questions_content_keeps_full_tail():
    assert extract_questions_content("/questions resolve q_1 已解决了", "") == "resolve q_1 已解决了"
    assert extract_questions_content("/questions", "frequent") == "frequent"


def test_format_unresolved_questions_empty_and_non_empty():
    empty = format_unresolved_questions("Alice", [])
    text = format_unresolved_questions("Alice", _questions())

    assert "没有未解决的问题" in empty
    assert "Alice 的未解决问题 (2 个)" in text
    assert "[q_1] Python 爬虫 怎么处理登录" in text
    assert "(问过 3 次)" in text


def test_format_all_questions_includes_stats_and_limit_notice():
    questions = [
        {"question_id": f"q_{i}", "question": f"问题{i}", "ask_count": i, "resolved": i % 2 == 0}
        for i in range(22)
    ]
    stats = {"total": 22, "resolved": 11, "unresolved": 11}

    text = format_all_questions("Alice", questions, stats)

    assert "总计: 22 | 已解决: 11 | 未解决: 11" in text
    assert "✅ [q_0] 问题0" in text
    assert "❓ [q_1] 问题1" in text
    assert "... 还有 2 个问题" in text


def test_format_frequent_questions_sorts_and_appends_docs():
    text = format_frequent_questions(
        "Alice",
        list(reversed(_questions())),
        related_docs=[{"title": "向量检索指南", "author": "Bob"}],
    )

    assert text.index("[q_1]") < text.index("[q_2]")
    assert "可以用 /partner 找相关领域的学习伙伴" in text
    assert "《向量检索指南》- Bob" in text


def test_related_doc_search_uses_question_keywords():
    doc_index = _DocIndex()

    docs = find_related_docs_for_questions(doc_index, _questions())

    assert docs == [{"title": "爬虫登录指南", "author": "Alice"}]
    assert doc_index.query[1] == 3
    assert extract_question_keywords(_questions())


def test_resolve_parsing_and_formatting():
    assert parse_resolve_args("q_1 已经看完文档") == ("q_1", "已经看完文档")
    assert parse_resolve_args("q_1") == ("q_1", "")
    assert questions_usage_for_resolve().startswith("用法: /questions resolve")
    assert format_resolve_question_result("q_1", True) == "✅ 问题 q_1 已标记为已解决"
    assert format_resolve_question_result("q_1", False) == "❌ 未找到问题 q_1"
    assert "未知操作: nope" in format_unknown_questions_action("nope")
