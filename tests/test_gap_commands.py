import pytest

from novabot.gap_commands import analyze_gap_command, validate_gap_request


class _Storage:
    def __init__(self, binding):
        self.binding = binding

    def get_binding(self, platform_id):
        assert platform_id == "u1"
        return self.binding


class _Analyzer:
    def __init__(self):
        self.calls = []

    async def analyze(self, *, yuque_id, target_domain=None, provider=None):
        self.calls.append(
            {
                "yuque_id": yuque_id,
                "target_domain": target_domain,
                "provider": provider,
            }
        )
        return {
            "target_domain": target_domain or "Python",
            "current_level": "intermediate",
            "missing_topics": [{"topic": "异步 IO", "priority": "high", "reason": "项目需要"}],
        }


def test_validate_gap_request_reports_missing_binding():
    yuque_id, error = validate_gap_request(
        storage=_Storage(None),
        platform_id="u1",
        provider=object(),
    )

    assert yuque_id == ""
    assert "请先绑定语雀账号" in error


def test_validate_gap_request_reports_broken_binding_or_missing_provider():
    broken_id, broken_error = validate_gap_request(
        storage=_Storage({"yuque_name": "Alice"}),
        platform_id="u1",
        provider=object(),
    )
    no_provider_id, no_provider_error = validate_gap_request(
        storage=_Storage({"yuque_id": 42}),
        platform_id="u1",
        provider=None,
    )

    assert broken_id == ""
    assert broken_error == "❌ 绑定信息异常，请重新绑定"
    assert no_provider_id == ""
    assert no_provider_error == "❌ LLM 未配置，无法分析"


def test_validate_gap_request_returns_bound_yuque_id():
    yuque_id, error = validate_gap_request(
        storage=_Storage({"yuque_id": 42}),
        platform_id="u1",
        provider=object(),
    )

    assert yuque_id == "42"
    assert error == ""


@pytest.mark.asyncio
async def test_analyze_gap_command_runs_analyzer_and_formats_report():
    analyzer = _Analyzer()
    provider = object()

    text = await analyze_gap_command(
        analyzer=analyzer,
        yuque_id="42",
        target_domain="Python",
        provider=provider,
    )

    assert analyzer.calls == [
        {"yuque_id": "42", "target_domain": "Python", "provider": provider}
    ]
    assert "📊 学习缺口分析：Python" in text
    assert "🎯 当前水平：进阶" in text
    assert "异步 IO" in text
