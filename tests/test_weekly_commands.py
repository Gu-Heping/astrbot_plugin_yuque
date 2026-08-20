from pathlib import Path

import pytest

from novabot.weekly_commands import handle_weekly_command


class _Reporter:
    def __init__(self):
        self.export_dir = None
        self.llm_call = None
        self.plain_calls = 0

    def export_weekly_raw_csv(self, output_dir):
        self.export_dir = output_dir
        return output_dir / "weekly_raw.csv", 7

    def generate_weekly_report(self):
        self.plain_calls += 1
        return "纯统计周报"

    async def generate_weekly_report_with_llm(self, *, provider, token_monitor=None):
        self.llm_call = {"provider": provider, "token_monitor": token_monitor}
        return "LLM 周报"


@pytest.mark.asyncio
async def test_weekly_raw_export_reports_sent_file(tmp_path):
    reporter = _Reporter()
    sent_paths = []

    async def send_file(path: Path):
        sent_paths.append(path)
        return True

    messages = await handle_weekly_command(
        reporter=reporter,
        action="raw",
        export_dir=tmp_path,
        send_file=send_file,
    )

    assert reporter.export_dir == tmp_path
    assert sent_paths == [tmp_path / "weekly_raw.csv"]
    assert messages == ["✅ 已导出按周原始数据\n记录周数: 7\n文件名: weekly_raw.csv"]


@pytest.mark.asyncio
async def test_weekly_raw_export_reports_path_when_file_send_unavailable(tmp_path):
    reporter = _Reporter()

    async def send_file(path: Path):
        return False

    messages = await handle_weekly_command(
        reporter=reporter,
        action="export",
        export_dir=tmp_path,
        send_file=send_file,
    )

    assert "当前平台不支持直接发文件" in messages[0]
    assert f"文件路径: {tmp_path / 'weekly_raw.csv'}" in messages[0]


@pytest.mark.asyncio
async def test_weekly_report_uses_llm_provider_when_available(tmp_path):
    reporter = _Reporter()
    provider = object()
    token_monitor = object()

    messages = await handle_weekly_command(
        reporter=reporter,
        action="",
        export_dir=tmp_path,
        provider=provider,
        token_monitor=token_monitor,
    )

    assert messages == ["LLM 周报"]
    assert reporter.llm_call == {"provider": provider, "token_monitor": token_monitor}
    assert reporter.plain_calls == 0


@pytest.mark.asyncio
async def test_weekly_report_falls_back_to_plain_stats_without_provider(tmp_path):
    reporter = _Reporter()

    messages = await handle_weekly_command(
        reporter=reporter,
        action="",
        export_dir=tmp_path,
    )

    assert messages == ["纯统计周报"]
    assert reporter.plain_calls == 1
