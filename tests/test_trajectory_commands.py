from __future__ import annotations

from novabot.trajectory_commands import (
    build_trajectory_topic_query,
    extract_trajectory_content,
    find_member_id_by_name,
    format_member_trajectory,
    format_topic_fallback,
    should_analyze_trajectory,
    trajectory_usage_for_topic,
)


def _trajectory(count=3):
    return [
        {
            "timestamp": "2026-01-02T03:04:05",
            "event_name": "发布文档",
            "title": "Python 爬虫登录指南",
        },
        {
            "timestamp": "bad-date",
            "event_name": "更新文档",
            "title": "向量检索 chunk 调优",
        },
        {
            "timestamp": "",
            "event_name": "活动",
            "title": "无日期记录",
        },
    ][:count]


def test_extract_trajectory_content_keeps_full_tail():
    assert extract_trajectory_content("/trajectory topic Python 爬虫", "") == "topic Python 爬虫"
    assert extract_trajectory_content("/trajectory", "Alice") == "Alice"


def test_find_member_id_by_name_matches_name_or_login_case_insensitive():
    members = {
        "42": {"name": "Alice", "login": "alice-dev"},
        7: {"name": "Bob", "login": "bobby"},
    }

    assert find_member_id_by_name(members, "Alice") == "42"
    assert find_member_id_by_name(members, "ALICE") == "42"
    assert find_member_id_by_name(members, "bobby") == "7"
    assert find_member_id_by_name(members, "Missing") == ""


def test_format_member_trajectory_dates_and_limit_notice():
    events = _trajectory(3) + [
        {"timestamp": "2026-01-03", "event_name": "活动", "title": str(i)}
        for i in range(10)
    ]

    text = format_member_trajectory("Alice", events)

    assert "【Alice 最近活动】" in text
    assert "01-02 - 发布文档：Python 爬虫登录指南" in text
    assert "bad-date - 更新文档：向量检索 chunk 调优" in text
    assert "未知 - 活动：无日期记录" in text
    assert "... 还有 3 条记录" in text


def test_topic_query_and_fallback_formatting():
    query = build_trajectory_topic_query("  Python 爬虫  ")
    assert query == "查看最近谁在做与「Python 爬虫」相关的事情"
    assert trajectory_usage_for_topic() == "用法: /trajectory topic <主题>"

    results = [
        {
            "member_id": "42",
            "match_count": 2,
            "matching_events": [{"event_name": "发布文档", "title": "Python 爬虫登录指南"}],
            "stats": {"doc_count": 1},
        }
    ]
    text = format_topic_fallback("Python", results, {"42": {"name": "Alice", "login": "alice"}})
    empty = format_topic_fallback("Python", [], {})

    assert "Alice（2 次相关活动）" in text
    assert "发布文档：Python 爬虫登录指南" in text
    assert "共 1 篇相关文档" in text
    assert empty == "最近 30 天没有成员在做「Python」相关的事情"


def test_should_analyze_trajectory_only_for_self_with_enough_events():
    assert should_analyze_trajectory(True, _trajectory(3))
    assert not should_analyze_trajectory(True, _trajectory(2))
    assert not should_analyze_trajectory(False, _trajectory(3))
