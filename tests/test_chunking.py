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


def test_split_markdown_long_block_does_not_drop_text_between_boundary_and_step():
    body = "甲" * 300 + "。" + "乙" * 520 + "关键内容" + "丙" * 400

    chunks = split_markdown("1", body, size=1000, overlap=180)
    combined = "".join(chunk.content for chunk in chunks)

    assert "关键内容" in combined
