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


def test_markdown_to_plaintext_handles_relaxed_pipe_tables():
    text = (
        "文档 | 类型 | 亮点\n"
        "《线下活动》 | 新建 | 1,389 字，社团线下活动安排\n"
        "《项目技术设计文档》 | 更新 | 技术方案迭代\n"
    )

    plain = markdown_to_plaintext(text)

    assert "文档 | 类型 | 亮点" in plain
    assert "《线下活动》 | 新建 | 1,389 字，社团线下活动安排" in plain
