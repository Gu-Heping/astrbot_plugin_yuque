"""Command helpers for weekly reports."""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Protocol


class WeeklyReporterLike(Protocol):
    def export_weekly_raw_csv(self, output_dir: Path) -> tuple[Path, int]: ...

    def generate_weekly_report(self) -> str: ...

    async def generate_weekly_report_with_llm(self, *, provider, token_monitor=None) -> str: ...


SendFileCallback = Callable[[Path], Awaitable[bool]]


async def handle_weekly_command(
    *,
    reporter: WeeklyReporterLike,
    action: str = "",
    export_dir: Path,
    provider=None,
    token_monitor=None,
    send_file: SendFileCallback | None = None,
) -> list[str]:
    """Execute /weekly and return user-facing messages."""
    if action.lower() in ("raw", "export"):
        csv_path, row_count = reporter.export_weekly_raw_csv(export_dir)
        sent = await send_file(csv_path) if send_file else False
        if sent:
            return [
                f"✅ 已导出按周原始数据\n"
                f"记录周数: {row_count}\n"
                f"文件名: {csv_path.name}"
            ]
        return [
            f"✅ 已导出按周原始数据（当前平台不支持直接发文件）\n"
            f"记录周数: {row_count}\n"
            f"文件路径: {csv_path}"
        ]

    if provider:
        return [
            await reporter.generate_weekly_report_with_llm(
                provider=provider,
                token_monitor=token_monitor,
            )
        ]

    return [reporter.generate_weekly_report()]
