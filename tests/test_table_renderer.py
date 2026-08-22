"""Tests for Markdown table rendering."""

from __future__ import annotations

import asyncio
import builtins
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from novabot.table_renderer import (
    _download_font,
    _extract_table_blocks,
    _find_font_path,
    _is_separator_row,
    _is_valid_font,
    _parse_row,
    _render_table_image,
    ensure_cjk_font,
    render_tables_as_images,
)


def test_render_tables_fallback_to_plain_text_when_no_font(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "novabot.table_renderer._find_font", lambda size, font_path=None: None
    )
    text = "| 配置 | 说明 |\n| --- | --- |\n| A | B |\n"
    segments = render_tables_as_images(text, tmp_path)
    assert len(segments) == 1
    assert segments[0][0] == "text"
    assert "配置" in segments[0][1]


def test_parse_simple_table():
    text = (
        "| 配置 | 必填 | 说明 |\n"
        "| --- | --- | --- |\n"
        "| token | 是 | API token |\n"
    )
    blocks = _extract_table_blocks(text)
    assert len(blocks) == 1
    start, end, rows = blocks[0]
    assert start == 0
    assert end == 3
    assert rows == [
        ["配置", "必填", "说明"],
        ["token", "是", "API token"],
    ]


def test_separator_row_detection():
    assert _is_separator_row(["---", ":--:", "---"])
    assert not _is_separator_row(["配置", "必填"])


def test_parse_row_strips_padding():
    assert _parse_row("|  a  | b |") == ["a", "b"]
    assert _parse_row("文档 | 类型 | 亮点") == ["文档", "类型", "亮点"]


def test_extract_ignores_invalid_tables():
    text = "| a | b |\n普通段落"
    assert _extract_table_blocks(text) == []


def test_extract_relaxed_pipe_table_without_separator():
    text = (
        "文档 | 类型 | 亮点\n"
        "《线下活动》 | 新建 | 1,389 字，社团线下活动安排\n"
        "《项目技术设计文档》 | 更新 | 技术方案迭代\n"
    )

    blocks = _extract_table_blocks(text)

    assert len(blocks) == 1
    assert blocks[0][2] == [
        ["文档", "类型", "亮点"],
        ["《线下活动》", "新建", "1,389 字，社团线下活动安排"],
        ["《项目技术设计文档》", "更新", "技术方案迭代"],
    ]


def test_extract_two_column_relaxed_pipe_table_without_separator():
    text = (
        "文档 | 类型\n"
        "《线下活动》 | 新建\n"
    )

    blocks = _extract_table_blocks(text)

    assert len(blocks) == 1
    assert blocks[0][2] == [
        ["文档", "类型"],
        ["《线下活动》", "新建"],
    ]


def test_extract_ignores_ordered_lists_with_pipes():
    text = "1. A | B\n2) C | D\n"

    assert _extract_table_blocks(text) == []


def test_render_table_image_creates_png(tmp_path):
    from PIL import ImageFont

    rows = [["Name", "Description"], ["token", "API token"]]
    output = tmp_path / "test.png"
    _render_table_image(rows, output, font_path=None)
    assert output.exists()
    from PIL import Image

    with Image.open(output) as img:
        assert img.format == "PNG"
        assert img.width > 0 and img.height > 0
    assert ImageFont.load_default()


def test_render_tables_as_images_splits_segments(tmp_path, monkeypatch):
    from PIL import ImageFont

    text = (
        "请看下表：\n"
        "\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| A | 1 |\n"
        "\n"
        "结束。"
    )
    monkeypatch.setattr(
        "novabot.table_renderer._find_font",
        lambda size, font_path=None: ImageFont.load_default(),
    )
    segments = render_tables_as_images(text, tmp_path)
    assert len(segments) == 3
    assert segments[0][0] == "text"
    assert "请看下表" in segments[0][1]
    assert segments[1][0] == "image"
    assert Path(segments[1][1]).exists()
    assert segments[2][0] == "text"
    assert "结束" in segments[2][1]


def test_render_tables_as_images_without_tables(tmp_path):
    text = "这是一段没有表格的文本。"
    segments = render_tables_as_images(text, tmp_path)
    assert len(segments) == 1
    assert segments[0] == ("text", "这是一段没有表格的文本。")


def test_render_tables_as_images_handles_multiple_tables(tmp_path):
    text = (
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        "中间文字\n"
        "| C | D |\n| --- | --- |\n| 3 | 4 |\n"
    )
    segments = render_tables_as_images(text, tmp_path)
    images = [s for s in segments if s[0] == "image"]
    assert len(images) == 2
    for _, path in images:
        assert Path(path).exists()


def test_render_relaxed_weekly_pipe_table_as_image(tmp_path, monkeypatch):
    from PIL import ImageFont

    monkeypatch.setattr(
        "novabot.table_renderer._find_font",
        lambda size, font_path=None: ImageFont.load_default(),
    )
    text = (
        "🔥 本周热点文档\n\n"
        "文档 | 类型 | 亮点\n"
        "《线下活动》 | 新建 | 1,389 字，社团线下活动安排\n"
        "《项目技术设计文档》 | 更新 | 技术方案迭代\n\n"
        "✍️ 活跃作者 TOP 5"
    )

    segments = render_tables_as_images(text, tmp_path)

    assert [segment[0] for segment in segments] == ["text", "image", "text"]
    assert Path(segments[1][1]).exists()


def test_render_ordered_lists_with_pipes_as_plain_text(tmp_path):
    text = "1. A | B\n2) C | D\n"

    segments = render_tables_as_images(text, tmp_path)

    assert segments == [("text", "1. A | B\n2) C | D")]
    assert list(tmp_path.glob("table_*.png")) == []


def test_find_font_path_prefers_configured_path(tmp_path, monkeypatch):
    font_file = tmp_path / "custom.ttf"
    font_file.write_bytes(b"dummy")
    monkeypatch.setattr("novabot.table_renderer._is_valid_font", lambda path: True)
    assert _find_font_path(str(font_file)) == str(font_file)


def test_ensure_cjk_font_returns_configured_path(tmp_path, monkeypatch):
    font_file = tmp_path / "configured.ttf"
    font_file.write_bytes(b"dummy")
    monkeypatch.setattr("novabot.table_renderer._is_valid_font", lambda path: True)
    result = asyncio.run(
        ensure_cjk_font(tmp_path, configured_font_path=str(font_file))
    )
    assert result == str(font_file)


def test_ensure_cjk_font_downloads_when_allowed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("novabot.table_renderer._find_font_path", lambda font_path=None: None)
    monkeypatch.setattr("novabot.table_renderer._is_valid_font", lambda path: True)

    async def fake_download(url, dest, timeout=120.0):
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"font")
        return True

    monkeypatch.setattr("novabot.table_renderer._download_font", fake_download)
    result = asyncio.run(ensure_cjk_font(tmp_path, allow_download=True))
    assert result is not None
    assert Path(result).name == "NotoSansCJKsc-Regular.otf"
    assert len(calls) >= 1


def test_ensure_cjk_font_skips_download_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr("novabot.table_renderer._find_font_path", lambda font_path=None: None)
    result = asyncio.run(ensure_cjk_font(tmp_path, allow_download=False))
    assert result is None


def test_is_valid_font_rejects_invalid_file(tmp_path):
    bad_file = tmp_path / "not_a_font.txt"
    bad_file.write_text("hello")
    assert _is_valid_font(bad_file) is False


def test_render_table_image_rejects_oversized_tables(tmp_path):
    rows = [["c"]] * 201

    with pytest.raises(RuntimeError, match="too many rows"):
        _render_table_image(rows, tmp_path / "large.png")


def test_render_tables_as_images_removes_partial_images_on_fallback(tmp_path, monkeypatch):
    calls = {"count": 0}

    def fake_render(rows, output_path, font_path=None):
        calls["count"] += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"partial")
        if calls["count"] == 2:
            raise RuntimeError("too large")
        return output_path

    monkeypatch.setattr("novabot.table_renderer._render_table_image", fake_render)
    text = (
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        "\n"
        "| C | D |\n| --- | --- |\n| 3 | 4 |\n"
    )

    segments = render_tables_as_images(text, tmp_path)

    assert segments == [("text", "A | B\n1 | 2\n\nC | D\n3 | 4")]
    assert list(tmp_path.glob("table_*.png")) == []


def test_clean_table_images_enforces_retention_count(tmp_path):
    for i in range(3):
        path = tmp_path / f"table_{i}.png"
        path.write_bytes(b"image")
        old_time = time.time() - 120 - i
        os.utime(path, (old_time, old_time))

    from novabot.table_renderer import clean_table_images

    clean_table_images(tmp_path, max_files=1, max_age_seconds=999999)

    assert len(list(tmp_path.glob("table_*.png"))) == 1


def test_clean_table_images_protects_recent_images_from_count_cleanup(tmp_path):
    for i in range(2):
        path = tmp_path / f"table_old_{i}.png"
        path.write_bytes(b"old")
        old_time = time.time() - 120 - i
        os.utime(path, (old_time, old_time))

    recent = tmp_path / "table_recent.png"
    recent.write_bytes(b"recent")

    from novabot.table_renderer import clean_table_images

    clean_table_images(tmp_path, max_files=0, max_age_seconds=999999)

    assert recent.exists()
    assert sorted(path.name for path in tmp_path.glob("table_*.png")) == [
        "table_recent.png"
    ]


def test_find_font_path_caches_missing_system_font(monkeypatch):
    import novabot.table_renderer as table_renderer

    table_renderer._SYSTEM_FONT_PATH_CACHE = None
    table_renderer._SYSTEM_FONT_PATH_CACHE_SET = False

    class MissingPath:
        def __init__(self, value):
            self.value = value

        def is_file(self):
            return False

    monkeypatch.setattr(table_renderer, "Path", MissingPath)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "matplotlib":
            raise ImportError("matplotlib unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert table_renderer._find_font_path() is None
    assert table_renderer._SYSTEM_FONT_PATH_CACHE_SET is True


def test_download_font_rejects_oversized_response(tmp_path, monkeypatch):
    import novabot.table_renderer as table_renderer

    class FakeResponse:
        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self, chunk_size=8192):
            yield b"x" * (table_renderer._MAX_FONT_BYTES + 1)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url):
            return FakeResponse()

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=FakeClient),
    )

    dest = tmp_path / "font.otf"
    assert asyncio.run(_download_font("https://example.test/font.otf", dest)) is False
    assert not dest.exists()
