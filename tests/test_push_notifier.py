import subprocess
from pathlib import Path

from novabot.push_notifier import PushNotifier


class _DummyContext:
    pass


class _DummySubscriptions:
    pass


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


def test_prepare_update_diff_keeps_metadata_only_diff_without_fake_body(tmp_path):
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

    assert prepared == diff
