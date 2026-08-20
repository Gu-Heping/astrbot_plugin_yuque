from __future__ import annotations

from novabot.collab_commands import (
    build_collab_find_query,
    collab_usage_for_find,
    extract_collab_content,
    format_collaborators,
    format_potential_collaborators,
)


class _DocIndex:
    def __init__(self):
        self.query = None

    def search(self, title, limit):
        self.query = (title, limit)
        return [
            {"title": "Python 协作项目", "creator_id": "42", "author": "Alice"},
            {"title": "其他文档", "creator_id": "7", "author": "Bob"},
        ]


def test_extract_collab_content_and_find_query():
    assert extract_collab_content("/collab find Python 爬虫", "") == "find Python 爬虫"
    assert extract_collab_content("/collab", "Alice") == "Alice"
    assert build_collab_find_query("  Python 爬虫  ") == (
        "我想找一个在「Python 爬虫」领域有经验的协作伙伴，能帮我一起做项目或请教问题"
    )
    assert collab_usage_for_find() == "用法: /collab find <主题>"


def test_format_potential_collaborators_resolves_member_and_doc():
    doc_index = _DocIndex()
    text = format_potential_collaborators(
        "Python",
        [
            {
                "member_id": "42",
                "match_score": 0.82,
                "match_reasons": ["最近写过相关文档", "同领域活跃"],
            }
        ],
        members={"42": {"name": "Alice", "login": "alice"}},
        doc_index=doc_index,
    )

    assert "Alice（匹配度 82%）" in text
    assert "✓ 最近写过相关文档" in text
    assert "相关文档：Python 协作项目" in text
    assert "https://www.yuque.com/alice" in text
    assert doc_index.query == ("Python", 5)


def test_format_collaborators_handles_empty_and_non_empty():
    empty = format_collaborators("Alice", [], stats={}, members={})
    text = format_collaborators(
        "Alice",
        [
            {
                "member_id": "7",
                "strength": 0.64,
                "source_name": "工程",
                "context": "共同维护部署文档",
            }
        ],
        stats={"collaborator_count": 3},
        members={7: {"name": "Bob", "login": "bob"}},
    )

    assert empty == "「Alice」暂无协作记录"
    assert "Bob（强度 64%，工程：共同维护部署文档）" in text
    assert "统计：3 位协作伙伴" in text
