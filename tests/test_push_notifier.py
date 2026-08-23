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
