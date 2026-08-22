from types import SimpleNamespace

import pytest

from novabot.git_ops import GitOps


def test_configure_user_identity_writes_local_git_config(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("novabot.git_ops.subprocess.run", fake_run)

    assert GitOps(tmp_path).configure_user_identity("NovaBot", "bot@example.local")
    assert [call[0] for call in calls] == [
        ["git", "config", "--local", "user.name", "NovaBot"],
        ["git", "config", "--local", "user.email", "bot@example.local"],
    ]
    assert all(call[1]["cwd"] == tmp_path for call in calls)


def test_configure_user_identity_rejects_unsafe_values(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "novabot.git_ops.subprocess.run",
        lambda cmd, **kwargs: calls.append(cmd),
    )

    assert not GitOps(tmp_path).configure_user_identity("Bad\nName", "bot@example.local")
    assert calls == []


def test_ensure_user_identity_can_auto_config_when_missing(tmp_path, monkeypatch):
    git = GitOps(tmp_path)
    states = {"has_identity": False}

    monkeypatch.setattr(git, "has_user_identity", lambda: states["has_identity"])

    def configure(name, email):
        assert name == "NovaBot"
        assert email == "bot@example.local"
        states["has_identity"] = True
        return True

    monkeypatch.setattr(git, "configure_user_identity", configure)

    assert git.ensure_user_identity(
        auto_config=True,
        name="NovaBot",
        email="bot@example.local",
    )


def test_ensure_user_identity_skips_auto_config_when_disabled(tmp_path, monkeypatch):
    git = GitOps(tmp_path)
    monkeypatch.setattr(git, "has_user_identity", lambda: False)
    monkeypatch.setattr(
        git,
        "configure_user_identity",
        lambda name, email: pytest.fail("should not configure"),
    )

    assert not git.ensure_user_identity(auto_config=False)
