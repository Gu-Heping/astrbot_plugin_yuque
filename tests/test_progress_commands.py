from __future__ import annotations

from novabot.progress_commands import (
    build_progress_overview,
    extract_progress_content,
    format_domain_progress,
    format_progress_overview_without_analysis,
    progress_usage_for_add,
    progress_usage_for_level,
    record_progress_milestone,
    set_progress_level,
)


class _MemoryManager:
    def __init__(self, progress=None):
        self.progress = progress or {}
        self.added = None
        self.level = None

    def get_learning_progress(self, user_id, domain=None):
        if domain:
            return self.progress.get(domain, {"level": "beginner", "milestones": []})
        return self.progress

    def add_learning_milestone(self, user_id, domain, event):
        self.added = (user_id, domain, event)
        return True

    def update_learning_level(self, user_id, domain, level):
        self.level = (user_id, domain, level)
        return True


def test_extract_progress_content_keeps_full_command_tail():
    assert extract_progress_content("/progress add 爬虫 完成 基础教程", "") == "add 爬虫 完成 基础教程"
    assert extract_progress_content("/progress", "算法") == "算法"


def test_build_progress_overview_handles_empty_and_non_empty_progress():
    empty = build_progress_overview(_MemoryManager(), "42", "Alice")
    assert "暂无学习记录" in empty.text
    assert empty.progress_for_analysis == {}

    progress = {
        "爬虫": {
            "level": "intermediate",
            "milestones": [{"date": "2026-01-02", "event": "完成基础教程"}],
        }
    }
    overview = build_progress_overview(_MemoryManager(progress), "42", "Alice")

    assert "爬虫: 进阶 (1 个里程碑)" in overview.text
    assert "正在分析学习趋势" in overview.text
    assert overview.progress_for_analysis == progress


def test_progress_overview_without_analysis_adds_detail_hint():
    text = format_progress_overview_without_analysis(
        "Alice",
        {"LLM": {"level": "advanced", "milestones": []}},
    )

    assert "LLM: 高级 (0 个里程碑)" in text
    assert "使用 /progress <领域> 查看详情" in text


def test_record_milestone_and_set_level_delegate_to_memory_manager():
    manager = _MemoryManager()

    add_text = record_progress_milestone(manager, "42", "爬虫", "完成基础")
    level_text = set_progress_level(manager, "42", "爬虫", "advanced")
    invalid_text = set_progress_level(manager, "42", "爬虫", "expert")

    assert manager.added == ("42", "爬虫", "完成基础")
    assert add_text == "✅ 已记录里程碑：爬虫 - 完成基础"
    assert manager.level == ("42", "爬虫", "advanced")
    assert level_text == "✅ 已设置「爬虫」等级为 高级"
    assert invalid_text == "等级必须是: beginner / intermediate / advanced"


def test_format_domain_progress_and_usage_texts():
    text = format_domain_progress(
        "Alice",
        "爬虫",
        {
            "level": "beginner",
            "milestones": [{"date": "2026-01-02", "event": "完成基础"}],
            "next_step": "写一个小项目",
        },
    )

    assert "Alice 的「爬虫」学习进度" in text
    assert "等级: 入门" in text
    assert "2026-01-02 - 完成基础" in text
    assert "下一步建议: 写一个小项目" in text
    assert progress_usage_for_add().startswith("用法: /progress add")
    assert "beginner(入门)" in progress_usage_for_level()
