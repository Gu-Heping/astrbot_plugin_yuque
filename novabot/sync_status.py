"""User-facing formatting for Yuque sync lifecycle state."""

from __future__ import annotations

from typing import Any


def format_sync_status(state: dict[str, Any]) -> str:
    """Render persisted sync state for the /sync status command."""

    if state.get("in_progress") and state.get("progress"):
        return _format_repo_sync_progress(state)

    if state.get("status") == "rag_indexing" and state.get("rag_progress"):
        rp = state["rag_progress"]
        return (
            "⏳ RAG 索引进行中\n"
            "━━━━━━━━━━━━━━━\n"
            f"进度: {rp.get('current', 0)}/{rp.get('total', 0)}\n\n"
            "（Embedding API 调用较慢，请耐心等待）\n"
            "使用 /sync status 刷新进度"
        )

    if state.get("status") == "chunk_indexing" and state.get("chunk_progress"):
        cp = state["chunk_progress"]
        return (
            "⏳ Chunk 索引进行中\n"
            "━━━━━━━━━━━━━━━\n"
            f"进度: {cp.get('current', 0)}/{cp.get('total', 0)}\n\n"
            "使用 /sync status 刷新进度"
        )

    if state.get("last_sync"):
        return _format_finished_sync(state)

    return "尚未同步，使用 /sync 开始"


def format_sync_already_running(state: dict[str, Any]) -> str:
    """Render the response when a new /sync command races an active sync."""

    p = state.get("progress") or {}
    return (
        "⏳ 同步已在进行中\n"
        f"进度: {p.get('current', 0)}/{p.get('total', 0)}\n"
        "使用 /sync status 查看进度"
    )


def format_sync_started(teams_count: int, team_id: str = "") -> str:
    """Render the response after scheduling a background sync."""

    if team_id:
        team_hint = f"（team_id={team_id}）"
    else:
        team_hint = f"（{teams_count} 个团队）" if teams_count > 1 else ""
    return f"🔄 同步已启动{team_hint}（后台运行）\n使用 /sync status 查看进度"


def _format_repo_sync_progress(state: dict[str, Any]) -> str:
    p = state["progress"]
    team_progress = state.get("team_progress") or {}
    team_line = ""
    if team_progress:
        team_line = (
            f"团队: {team_progress.get('team_name') or team_progress.get('team_id')} "
            f"({team_progress.get('current', 0)}/{team_progress.get('total', 0)})\n"
        )
    return (
        "⏳ 同步进行中\n"
        "━━━━━━━━━━━━━━━\n"
        f"{team_line}"
        f"进度: {p.get('current', 0)}/{p.get('total', 0)}\n"
        f"当前: {p.get('current_repo', '')}\n\n"
        "使用 /sync status 刷新进度"
    )


def _format_finished_sync(state: dict[str, Any]) -> str:
    lines = [
        "📊 同步状态",
        "━━━━━━━━━━━━━━━",
        f"上次同步: {str(state['last_sync'])[:19]}",
        f"知识库数: {state.get('repos_count', 0)}",
        f"文档总数: {state.get('docs_count', 0)}",
        f"Token 类型: {state.get('token_type', '未知')}",
    ]
    teams = state.get("teams") or {}
    if teams:
        lines.append("")
        lines.append("团队:")
        for team_id, info in teams.items():
            lines.append(
                f"- {info.get('team_name') or team_id}: "
                f"{info.get('repos_count', 0)} 个知识库, {info.get('docs_count', 0)} 篇文档"
            )
    return "\n".join(lines)
