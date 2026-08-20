from novabot.chunking import split_markdown


def test_split_markdown_strips_metadata_and_keeps_stable_ids():
    body = """---
id: 1
---

| 作者 | 更新时间 |
|------|----------|
| Alice | 2026-01-01 |

# 入门

第一段介绍 NovaBot 的知识检索。

## 细节

第二段说明 team 和 repository 组合过滤。
"""
    chunks_a = split_markdown("1", body, title="指南", team_id="nova", team_name="NOVA", size=220, overlap=40)
    chunks_b = split_markdown("1", body, title="指南", team_id="nova", team_name="NOVA", size=220, overlap=40)

    assert [c.chunk_id for c in chunks_a] == [c.chunk_id for c in chunks_b]
    assert chunks_a[0].team_id == "nova"
    assert "作者" not in chunks_a[0].content
    assert "入门" in chunks_a[0].content
