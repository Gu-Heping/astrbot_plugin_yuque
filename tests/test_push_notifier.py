import asyncio
import subprocess
from pathlib import Path

from novabot.push_notifier import NO_BODY_CHANGE, PushNotifier


class _DummyContext:
    pass


class _DummySubscriptions:
    pass


class _ProviderShouldNotBeUsed:
    def get_using_provider(self):
        raise AssertionError("provider should not be used")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "--short", "HEAD")


def _notifier(docs_dir: Path, data_dir: Path) -> PushNotifier:
    return PushNotifier(
        docs_dir=docs_dir,
        data_dir=data_dir,
        context=_DummyContext(),
        subscription_manager=_DummySubscriptions(),
        config={},
    )


def test_get_diff_uses_multiple_paths_for_renamed_document(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _git(docs_dir, "init")
    _git(docs_dir, "config", "user.name", "NovaBot")
    _git(docs_dir, "config", "user.email", "novabot@example.local")

    old_path = "team/repo/Old title.md"
    new_path = "team/repo/New title.md"
    old_file = docs_dir / old_path
    old_file.parent.mkdir(parents=True)
    old_file.write_text("line one\nline two\n", encoding="utf-8")
    first_commit = _commit(docs_dir, "initial")

    (data_dir / "last_push.json").write_text(
        '{"team:42": "' + first_commit + '"}',
        encoding="utf-8",
    )

    old_file.unlink()
    (docs_dir / new_path).write_text("line one\nline two changed\n", encoding="utf-8")
    second_commit = _commit(docs_dir, "rename and update")

    diff, is_first = _notifier(docs_dir, data_dir).get_diff(
        "team:42",
        second_commit,
        [old_path, new_path],
    )

    assert not is_first
    assert "不代表从空文件新建" in diff
    assert "+line two changed" in diff


def test_get_diff_without_push_record_falls_back_to_parent_commit(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _git(docs_dir, "init")
    _git(docs_dir, "config", "user.name", "NovaBot")
    _git(docs_dir, "config", "user.email", "novabot@example.local")

    doc_path = "team/repo/Guide.md"
    doc_file = docs_dir / doc_path
    doc_file.parent.mkdir(parents=True)
    doc_file.write_text("old content\n", encoding="utf-8")
    _commit(docs_dir, "initial")

    doc_file.write_text("new content\n", encoding="utf-8")
    second_commit = _commit(docs_dir, "update")

    diff, is_first = _notifier(docs_dir, data_dir).get_diff("team:42", second_commit, doc_path)

    assert not is_first
    assert "-old content" in diff
    assert "+new content" in diff


def test_get_diff_without_push_record_keeps_new_document_as_first_push(tmp_path):
    docs_dir = tmp_path / "docs"
    data_dir = tmp_path / "data"
    docs_dir.mkdir()
    data_dir.mkdir()
    _git(docs_dir, "init")
    _git(docs_dir, "config", "user.name", "NovaBot")
    _git(docs_dir, "config", "user.email", "novabot@example.local")

    seed_path = docs_dir / "team/repo/Seed.md"
    seed_path.parent.mkdir(parents=True)
    seed_path.write_text("seed\n", encoding="utf-8")
    _commit(docs_dir, "initial")

    doc_path = "team/repo/New.md"
    (docs_dir / doc_path).write_text("short\n", encoding="utf-8")
    second_commit = _commit(docs_dir, "new short document")

    diff, is_first = _notifier(docs_dir, data_dir).get_diff("team:43", second_commit, doc_path)

    assert is_first
    assert diff == "[这是新发布的文档，首次推送，无历史 diff 信息]"


def test_prepare_update_diff_prefers_body_changes_over_paths_and_metadata(tmp_path):
    notifier = _notifier(tmp_path / "docs", tmp_path / "data")
    diff = """[文档路径或标题发生变化；以下是同一篇文档在旧路径与新路径之间的 diff，不代表从空文件新建]

diff --git a/team/repo/old.md b/team/repo/new.md
similarity index 87%
rename from team/repo/old.md
rename to team/repo/new.md
@@ -1,8 +1,12 @@
 ---
-title: 旧标题
+title: 新标题
-updated_at: 2026-08-20
+updated_at: 2026-08-25
 ---
+## 课堂追问策略
+这次补充了课前预习、课堂提问和课后复盘三段式方法。
+每位成员需要记录一个可追问的问题，并在复盘中写出自己的判断。
"""

    prepared = notifier._prepare_update_diff_for_llm(diff)

    assert "课堂追问策略" in prepared
    assert "三段式方法" in prepared
    assert "路径移动" in prepared
    assert "title:" not in prepared
    assert "updated_at:" not in prepared
    assert "rename from" not in prepared


def test_prepare_update_diff_marks_metadata_only_diff_as_no_body_change(tmp_path):
    notifier = _notifier(tmp_path / "docs", tmp_path / "data")
    diff = """diff --git a/team/repo/a.md b/team/repo/b.md
rename from team/repo/a.md
rename to team/repo/b.md
@@ -1,5 +1,5 @@
 ---
-updated_at: 2026-08-20
+updated_at: 2026-08-25
 ---
"""

    prepared = notifier._prepare_update_diff_for_llm(diff)

    assert prepared == NO_BODY_CHANGE
    assert notifier.pre_check(prepared, is_first_push=False) == (True, "无正文变更")


def test_agent_should_push_skips_metadata_only_diff_before_provider(tmp_path):
    notifier = PushNotifier(
        docs_dir=tmp_path / "docs",
        data_dir=tmp_path / "data",
        context=_ProviderShouldNotBeUsed(),
        subscription_manager=_DummySubscriptions(),
        config={},
    )
    diff = """diff --git a/team/repo/a.md b/team/repo/b.md
rename from team/repo/a.md
rename to team/repo/b.md
@@ -1,5 +1,5 @@
 ---
-updated_at: 2026-08-20
+updated_at: 2026-08-25
 ---
"""

    should_push, summary = asyncio.run(notifier.agent_should_push({}, diff, is_first_push=False))

    assert not should_push
    assert summary == {"highlights": [], "reason": "只有路径或元数据变化，正文信息不足"}


def test_prepare_update_diff_filters_generated_metadata_table(tmp_path):
    notifier = _notifier(tmp_path / "docs", tmp_path / "data")
    diff = """diff --git a/team/repo/a.md b/team/repo/a.md
@@ -1,11 +1,11 @@
 ---
 title: 文档
-updated_at: 2026-08-20
+updated_at: 2026-08-25
 ---
 
 | 作者 | 创建时间 | 更新时间 |
 | --- | --- | --- |
-| Old Name | 2026-08-01 | 2026-08-20 |
+| New Name | 2026-08-01 | 2026-08-25 |
 
 正文没有变化
"""

    prepared = notifier._prepare_update_diff_for_llm(diff)

    assert prepared == NO_BODY_CHANGE


def test_prepare_update_diff_filters_frontmatter_when_hunk_starts_after_line_one(tmp_path):
    notifier = _notifier(tmp_path / "docs", tmp_path / "data")
    diff = """diff --git a/team/repo/a.md b/team/repo/a.md
@@ -3,6 +3,6 @@
 slug: demo
-updated_at: 2026-08-20
+updated_at: 2026-08-25
 word_count: 12
 ---
 
"""

    prepared = notifier._prepare_update_diff_for_llm(diff)

    assert prepared == NO_BODY_CHANGE


def test_prepare_update_diff_keeps_body_lines_starting_like_diff_headers(tmp_path):
    notifier = _notifier(tmp_path / "docs", tmp_path / "data")
    diff = """diff --git a/team/repo/a.md b/team/repo/a.md
@@ -20,2 +20,2 @@
---old_counter
+++new_counter
"""

    prepared = notifier._prepare_update_diff_for_llm(diff)

    assert "--old_counter" in prepared
    assert "++new_counter" in prepared


def test_prepare_update_diff_keeps_yaml_like_body_content(tmp_path):
    notifier = _notifier(tmp_path / "docs", tmp_path / "data")
    diff = """diff --git a/team/repo/a.md b/team/repo/a.md
@@ -10,3 +10,6 @@
 正文段落
+```yaml
+title: 示例标题
+updated_at: 这里是正文示例，不是 frontmatter
+```
"""

    prepared = notifier._prepare_update_diff_for_llm(diff)

    assert "title: 示例标题" in prepared
    assert "updated_at: 这里是正文示例" in prepared


def test_prepare_update_diff_budget_keeps_added_and_removed_content(tmp_path):
    notifier = PushNotifier(
        docs_dir=tmp_path / "docs",
        data_dir=tmp_path / "data",
        context=_DummyContext(),
        subscription_manager=_DummySubscriptions(),
        config={"push_max_content_len": 900},
    )
    added = "\n".join(f"+新增正文内容 {i} " + "A" * 40 for i in range(30))
    removed = "\n".join(f"-旧正文内容 {i} " + "B" * 30 for i in range(12))
    diff = f"""diff --git a/team/repo/a.md b/team/repo/a.md
@@ -1,20 +1,30 @@
{removed}
{added}
"""

    prepared = notifier._prepare_update_diff_for_llm(diff)

    assert len(prepared) <= notifier.max_content_len
    assert "新增正文内容" in prepared
    assert "旧正文内容" in prepared


def test_prepare_update_diff_tiny_budget_does_not_expand_unbounded_text(tmp_path):
    notifier = PushNotifier(
        docs_dir=tmp_path / "docs",
        data_dir=tmp_path / "data",
        context=_DummyContext(),
        subscription_manager=_DummySubscriptions(),
        config={"push_max_content_len": 240},
    )
    added = "+新增正文内容 " + "A" * 500
    removed = "-旧正文内容 " + "B" * 500
    diff = f"""diff --git a/team/repo/a.md b/team/repo/a.md
@@ -30,1 +30,1 @@
{removed}
{added}
"""

    prepared = notifier._prepare_update_diff_for_llm(diff)

    assert len(prepared) <= notifier.max_content_len
    assert len(prepared) < 400
