"""Command helpers for learning gap analysis."""

from __future__ import annotations

from typing import Protocol

from .knowledge_gap import format_gap_report


class StorageLike(Protocol):
    def get_binding(self, platform_id: str) -> dict | None: ...


class GapAnalyzerLike(Protocol):
    async def analyze(self, *, yuque_id: str, target_domain: str | None = None, provider=None) -> dict: ...


def validate_gap_request(*, storage: StorageLike, platform_id: str, provider) -> tuple[str, str]:
    """Return (yuque_id, error_message) for /gap."""
    binding = storage.get_binding(platform_id)
    if not binding:
        return "", "❌ 请先绑定语雀账号\n使用 /bind <语雀用户名> 绑定后，才能分析你的学习缺口。"

    yuque_id = binding.get("yuque_id")
    if not yuque_id:
        return "", "❌ 绑定信息异常，请重新绑定"

    if not provider:
        return "", "❌ LLM 未配置，无法分析"

    return str(yuque_id), ""


async def analyze_gap_command(
    *,
    analyzer: GapAnalyzerLike,
    yuque_id: str,
    target_domain: str = "",
    provider,
) -> str:
    """Run the learning gap analyzer and format the report."""
    gap = await analyzer.analyze(
        yuque_id=yuque_id,
        target_domain=target_domain or None,
        provider=provider,
    )
    return format_gap_report(gap)
