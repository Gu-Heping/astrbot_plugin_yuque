from __future__ import annotations

from novabot.partner_commands import (
    build_partner_agent_query,
    find_partner_fallback,
    partner_missing_profile_message,
)


class _Matcher:
    def __init__(self, partners=None, mentors=None):
        self.partners = partners or []
        self.mentors = mentors or []
        self.calls = []

    def find_partners(self, yuque_id, topic):
        self.calls.append(("partners", yuque_id, topic))
        return self.partners

    def find_mentors(self, yuque_id, topic):
        self.calls.append(("mentors", yuque_id, topic))
        return self.mentors


class _Storage:
    def get_docs_by_author(self, name, yuque_id):
        return [{"title": "文档"}]


def test_partner_missing_profile_and_agent_query():
    assert partner_missing_profile_message() == "⚠️ 你还没有画像\n使用 /profile refresh 生成画像后再来找我推荐伙伴"
    assert build_partner_agent_query("") == "请根据我的兴趣推荐学习伙伴或导师"
    assert build_partner_agent_query("  Python 爬虫  ") == "我想找一个在「Python 爬虫」领域的学习伙伴或导师"


def test_find_partner_fallback_formats_matches():
    matcher = _Matcher(
        partners=[
            {
                "name": "Alice",
                "login": "alice",
                "common_interests": ["python"],
                "level": "intermediate",
            }
        ],
        mentors=[
            {
                "name": "Bob",
                "login": "bob",
                "topic_level": "advanced",
                "related_docs": ["Python 指南"],
            }
        ],
    )

    text = find_partner_fallback(
        matcher=matcher,
        storage=_Storage(),
        yuque_id=42,
        topic="Python",
    )

    assert ("partners", 42, "Python") in matcher.calls
    assert ("mentors", 42, "Python") in matcher.calls
    assert "学习伙伴推荐：Python" in text
    assert "Alice (@alice)" in text
    assert "Bob (@bob)" in text


def test_find_partner_fallback_empty_messages():
    assert (
        find_partner_fallback(matcher=_Matcher(), storage=_Storage(), yuque_id=42, topic="Python")
        == "未找到「Python」相关的学习伙伴"
    )
    assert (
        find_partner_fallback(matcher=_Matcher(), storage=_Storage(), yuque_id=42)
        == "暂无匹配的学习伙伴"
    )
