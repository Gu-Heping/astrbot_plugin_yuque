from novabot.card_commands import generate_card_command, validate_card_request
import pytest


class _Generator:
    def __init__(self):
        self.calls = []

    async def generate(self, topic, provider):
        self.calls.append((topic, provider))
        return {
            "topic": topic,
            "core_knowledge": ["核心点"],
            "source_docs": [{"title": "部署说明", "author": "Alice"}],
        }


def test_validate_card_request_reports_missing_inputs():
    assert validate_card_request("", provider=object(), rag=object()) == "用法: /card <主题>\n例如: /card 爬虫"
    assert validate_card_request("爬虫", provider=None, rag=object()) == "❌ LLM 未配置，无法生成知识卡片"
    assert validate_card_request("爬虫", provider=object(), rag=None) == "❌ RAG 引擎未初始化，请先执行 /sync"
    assert validate_card_request("爬虫", provider=object(), rag=object()) == ""


@pytest.mark.asyncio
async def test_generate_card_command_uses_generator_and_formats_card():
    generator = _Generator()
    provider = object()

    text = await generate_card_command(
        topic="多团队检索",
        provider=provider,
        rag=object(),
        generator=generator,
    )

    assert generator.calls == [("多团队检索", provider)]
    assert "📚 知识卡片：多团队检索" in text
    assert "• 核心点" in text
    assert "《部署说明》- Alice" in text
