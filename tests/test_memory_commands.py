from __future__ import annotations

from novabot.memory_commands import (
    build_memory_overview,
    extract_memory_search_keyword,
    format_memory_clear_result,
    format_memory_search_results,
    format_recent_memory,
    resolve_bound_memory_user,
)


class _Storage:
    def __init__(self, binding):
        self.binding = binding

    def get_binding(self, platform_id):
        return self.binding


class _MemoryManager:
    def __init__(self):
        self.sessions = [
            {"started_at": "2026-01-02T03:04:05", "summary": "聊部署流程"},
            {"started_at": "bad-date", "summary": "聊检索"},
            {"started_at": "", "summary": "无日期"},
        ]

    def get_user_stats(self, user_id):
        return {"total_sessions": 3, "total_messages": 6, "recent_7_days": 2}

    def get_recent_sessions(self, user_id, limit=10):
        return self.sessions[:limit]


def test_resolve_bound_memory_user_handles_binding_states():
    user, error = resolve_bound_memory_user(_Storage(None), "u1")
    assert user is None
    assert error == "请先绑定账号：/bind <用户名>"

    user, error = resolve_bound_memory_user(_Storage({"yuque_name": "Alice"}), "u1")
    assert user is None
    assert error == "绑定信息异常，请重新绑定"

    user, error = resolve_bound_memory_user(
        _Storage({"yuque_id": 42, "yuque_name": "Alice"}),
        "u1",
    )
    assert error is None
    assert user.user_id == "42"
    assert user.yuque_name == "Alice"


def test_build_memory_overview_requests_analysis_when_enough_sessions():
    overview = build_memory_overview(_MemoryManager(), "42", "Alice")

    assert "Alice 的记忆概览" in overview.text
    assert "总会话数: 3" in overview.text
    assert "正在分析对话模式" in overview.text
    assert len(overview.sessions_for_analysis) == 3


def test_format_recent_memory_and_search_results():
    sessions = _MemoryManager().sessions

    recent = format_recent_memory("Alice", sessions)
    assert "[01-02 03:04] 聊部署流程" in recent
    assert "[bad-date] 聊检索" in recent
    assert "[未知日期] 无日期" in recent

    found = format_memory_search_results("部署", sessions[:1])
    empty = format_memory_search_results("不存在", [])

    assert "搜索「部署」的结果" in found
    assert "找到 1 条相关对话" in found
    assert empty == "未找到包含「不存在」的对话"


def test_memory_search_keyword_and_clear_result_formatting():
    assert extract_memory_search_keyword("/memory search 多词 关键词", "ignored") == "多词 关键词"
    assert extract_memory_search_keyword("/memory search", "fallback") == "fallback"
    assert format_memory_clear_result("Alice", True) == "✅ 已清除 Alice 的记忆"
    assert format_memory_clear_result("Alice", False) == "❌ 清除失败，请稍后重试"
