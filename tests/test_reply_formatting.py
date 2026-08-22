from novabot.reply_formatting import markdown_to_plaintext


def test_markdown_to_plaintext_removes_common_markers():
    text = (
        "# 标题\n\n"
        "**重点** 和 `代码`\n"
        "[文档](https://example.com)\n"
        "> 引用\n"
        "```python\nprint('hi')\n```"
    )

    plain = markdown_to_plaintext(text)

    assert "#" not in plain
    assert "**" not in plain
    assert "`" not in plain
    assert "重点 和 代码" in plain
    assert "文档 (https://example.com)" in plain
    assert "print('hi')" in plain


def test_markdown_to_plaintext_converts_tables_to_readable_rows():
    text = (
        "| 配置 | 说明 |\n"
        "| --- | --- |\n"
        "| render_tables_as_images | 表格转图片 |\n"
    )

    plain = markdown_to_plaintext(text)

    assert "---" not in plain
    assert "配置 | 说明" in plain
    assert "render_tables_as_images | 表格转图片" in plain
