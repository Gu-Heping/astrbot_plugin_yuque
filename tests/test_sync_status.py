from __future__ import annotations

from novabot.sync_status import (
    format_sync_already_running,
    format_sync_started,
    format_sync_status,
)


def test_format_sync_status_renders_team_progress():
    text = format_sync_status(
        {
            "in_progress": True,
            "progress": {"current": 2, "total": 5, "current_repo": "工程"},
            "team_progress": {
                "team_id": "other",
                "team_name": "Other",
                "current": 1,
                "total": 2,
            },
        }
    )

    assert "同步进行中" in text
    assert "团队: Other (1/2)" in text
    assert "进度: 2/5" in text
    assert "当前: 工程" in text


def test_format_sync_status_renders_chunk_progress_before_last_sync():
    text = format_sync_status(
        {
            "status": "chunk_indexing",
            "chunk_progress": {"current": 3, "total": 9},
            "last_sync": "2026-01-01T00:00:00+00:00",
        }
    )

    assert "Chunk 索引进行中" in text
    assert "进度: 3/9" in text
    assert "同步状态" not in text


def test_format_sync_status_renders_finished_multi_team_summary():
    text = format_sync_status(
        {
            "last_sync": "2026-01-02T03:04:05+00:00",
            "repos_count": 4,
            "docs_count": 12,
            "token_type": "多团队",
            "teams": {
                "default": {"team_name": "NOVA", "repos_count": 3, "docs_count": 10},
                "other": {"team_name": "Other", "repos_count": 1, "docs_count": 2},
            },
        }
    )

    assert "上次同步: 2026-01-02T03:04:05" in text
    assert "知识库数: 4" in text
    assert "- NOVA: 3 个知识库, 10 篇文档" in text
    assert "- Other: 1 个知识库, 2 篇文档" in text


def test_format_sync_start_and_race_messages():
    assert "2 个团队" in format_sync_started(2)
    assert "2 个团队" not in format_sync_started(1)
    assert "team_id=other" in format_sync_started(1, team_id="other")
    assert "进度: 0/0" in format_sync_already_running({})
