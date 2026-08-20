from __future__ import annotations

import pytest

from novabot.profile import (
    assess_user_domain,
    format_generated_profile_summary,
    format_profile_view,
    get_profile_docs,
    refresh_user_profile,
)


class _Storage:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.saved = []

    def get_docs_by_author(self, author_name=None, yuque_id=None):
        self.last_author = author_name
        self.last_yuque_id = yuque_id
        return list(self.docs)

    def save_profile(self, yuque_id, profile):
        self.saved.append((yuque_id, profile))


class _ProfileGenerator:
    async def generate_with_llm(self, docs, provider):
        self.generate_call = (docs, provider)
        return {
            "profile": {
                "interests": ["Python"],
                "level": "intermediate",
                "tags": ["学习者"],
                "summary": "正在进阶。",
            }
        }

    async def assess_domain_level(self, docs, domain, provider):
        self.assess_call = (docs, domain, provider)
        return {
            "domain": domain,
            "level": "advanced",
            "mastered": ["核心概念"],
            "next_steps": ["继续实践"],
        }


def test_format_profile_view_prompts_refresh_when_missing_profile():
    text = format_profile_view(
        binding={"yuque_login": "alice", "yuque_name": "Alice"},
        profile=None,
    )

    assert "📋 用户画像" in text
    assert "账号: @alice (Alice)" in text
    assert "画像未生成" in text
    assert "使用 /profile refresh 生成画像" in text


def test_format_profile_view_renders_skills_tags_stats_and_summary():
    text = format_profile_view(
        binding={"yuque_login": "alice", "yuque_name": "Alice"},
        profile={
            "profile": {
                "interests": ["Python", "RAG"],
                "skills": {"Python": "advanced", "RAG 检索": "intermediate"},
                "tags": ["后端", "知识库"],
                "level": "advanced",
                "summary": "持续建设知识库检索。",
            },
            "stats": {
                "docs_count": 12,
                "repos": ["工程", "AI", "社区", "归档"],
            },
        },
    )

    assert "• Python (高级)" in text
    assert "• RAG (进阶)" in text
    assert "• 后端 • 知识库" in text
    assert "• 文档数: 12 篇" in text
    assert "• 知识库: 工程, AI, 社区 等 4 个" in text
    assert "• 整体水平: 高级" in text
    assert "持续建设知识库检索。" in text


def test_format_profile_view_defaults_unknown_interest_to_beginner():
    text = format_profile_view(
        binding={"yuque_login": "alice", "yuque_name": "Alice"},
        profile={
            "profile": {
                "interests": ["前端"],
                "skills": {},
                "level": "beginner",
            },
            "stats": {},
        },
    )

    assert "• 前端 (入门)" in text
    assert "• 知识库: 暂无" in text


def test_format_generated_profile_summary_renders_refresh_result():
    text = format_generated_profile_summary(
        {
            "profile": {
                "interests": ["Python", "检索"],
                "level": "intermediate",
                "tags": ["学习者"],
                "summary": "正在进阶。",
            }
        }
    )

    assert "✅ 画像已生成" in text
    assert "兴趣: Python, 检索" in text
    assert "水平: 进阶" in text
    assert "标签: 学习者" in text
    assert "正在进阶。" in text


def test_get_profile_docs_uses_binding_name_and_yuque_id():
    storage = _Storage(docs=[{"title": "A"}])
    docs = get_profile_docs(
        storage=storage,
        binding={"yuque_name": "Alice", "yuque_id": 42},
    )

    assert docs == [{"title": "A"}]
    assert storage.last_author == "Alice"
    assert storage.last_yuque_id == 42


@pytest.mark.asyncio
async def test_refresh_user_profile_generates_and_saves_profile():
    storage = _Storage(docs=[{"title": "A"}])
    generator = _ProfileGenerator()

    docs_count, text = await refresh_user_profile(
        storage=storage,
        profile_generator=generator,
        binding={"yuque_name": "Alice", "yuque_id": 42},
        provider="provider",
    )

    assert docs_count == 1
    assert "✅ 画像已生成" in text
    assert generator.generate_call == ([{"title": "A"}], "provider")
    assert storage.saved == [(42, {
        "profile": {
            "interests": ["Python"],
            "level": "intermediate",
            "tags": ["学习者"],
            "summary": "正在进阶。",
        }
    })]


@pytest.mark.asyncio
async def test_refresh_user_profile_handles_missing_docs_or_provider():
    no_docs = await refresh_user_profile(
        storage=_Storage(),
        profile_generator=_ProfileGenerator(),
        binding={"yuque_name": "Alice", "yuque_id": 42},
        provider="provider",
    )
    no_provider = await refresh_user_profile(
        storage=_Storage(docs=[{"title": "A"}]),
        profile_generator=_ProfileGenerator(),
        binding={"yuque_name": "Alice", "yuque_id": 42},
        provider=None,
    )

    assert no_docs == (0, "⚠️ 未找到你的文档，请先执行 /sync 同步")
    assert no_provider == (1, "❌ LLM 未配置，请先配置模型 Provider")


@pytest.mark.asyncio
async def test_assess_user_domain_formats_assessment():
    storage = _Storage(docs=[{"title": "A"}])
    generator = _ProfileGenerator()

    docs_count, text = await assess_user_domain(
        storage=storage,
        profile_generator=generator,
        binding={"yuque_name": "Alice", "yuque_id": 42},
        domain="RAG",
        provider="provider",
    )

    assert docs_count == 1
    assert "📊 RAG 领域评估：高级" in text
    assert "核心概念" in text
    assert "继续实践" in text
    assert generator.assess_call[1] == "RAG"
