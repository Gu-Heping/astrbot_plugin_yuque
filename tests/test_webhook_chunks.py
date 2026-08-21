import pytest

from novabot.chunk_store import ChunkStore
from novabot.chunking import split_markdown
from novabot.doc_index import DocIndex
from novabot.webhook import WebhookHandler


def _handler(tmp_path, store):
    docs_dir = tmp_path / "yuque_docs"
    docs_dir.mkdir()
    return WebhookHandler(
        docs_dir=docs_dir,
        data_dir=tmp_path,
        get_client=lambda: None,
        rag=None,
        config={"git_enabled": False},
        chunk_store=store,
    )


def test_webhook_reads_team_info_from_repos_cache(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    handler = _handler(tmp_path, store)
    (handler.docs_dir / ".repos.json").write_text(
        '[{"id":7,"team_id":"nova","team_name":"NOVA","name":"工程","namespace":"nova/eng"}]',
        encoding="utf-8",
    )

    assert handler._get_team_info(repo_id=7) == {"team_id": "nova", "team_name": "NOVA"}
    assert handler._get_team_info(repo_id=8) == {"team_id": "default", "team_name": "NOVA"}


def test_webhook_prefers_repo_id_before_duplicate_repo_name(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    handler = _handler(tmp_path, store)
    (handler.docs_dir / ".repos.json").write_text(
        """
        [
          {"id":7,"team_id":"wrong","team_name":"Wrong","name":"工程","namespace":"wrong/eng"},
          {"id":8,"team_id":"nova","team_name":"NOVA","name":"工程","namespace":"nova/eng"}
        ]
        """,
        encoding="utf-8",
    )

    assert handler._get_team_info(repo_id=8, repo_name="工程") == {
        "team_id": "nova",
        "team_name": "NOVA",
    }


def test_webhook_resolves_doc_output_with_team_prefix(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    handler = _handler(tmp_path, store)

    _, default_file, default_rel = handler._resolve_doc_output(
        {
            "id": 1,
            "title": "默认团队文档",
            "slug": "default-doc",
            "team_id": "default",
        },
        repo_name="工程",
        namespace="nova/eng",
        toc_list=[],
    )
    repo_dir, other_file, other_rel = handler._resolve_doc_output(
        {
            "id": 2,
            "title": "其他团队文档",
            "slug": "other-doc",
            "team_id": "other",
        },
        repo_name="工程",
        namespace="other/eng",
        toc_list=[],
    )

    assert default_rel == "工程/默认团队文档.md"
    assert default_file == handler.docs_dir / "工程" / "默认团队文档.md"
    assert other_rel == "other/工程/其他团队文档.md"
    assert other_file == handler.docs_dir / "other" / "工程" / "其他团队文档.md"
    assert repo_dir == handler.docs_dir / "other" / "工程"


def test_webhook_resolves_repo_dir_from_team_prefixed_path(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    handler = _handler(tmp_path, store)

    assert handler._repo_dir_from_doc_path("工程/部署.md") == handler.docs_dir / "工程"
    assert (
        handler._repo_dir_from_doc_path("other/工程/部署.md", team_id="other")
        == handler.docs_dir / "other" / "工程"
    )


class _FakeYuqueClient:
    async def get_repo_toc(self, repo_id):
        assert repo_id == 7
        return [
            {
                "id": 42,
                "uuid": "doc-42",
                "type": "DOC",
                "title": "部署说明",
                "url": "deploy",
            }
        ]

    async def get_doc_detail(self, repo_id, slug):
        assert repo_id == 7
        assert slug == "deploy"
        return {
            "id": 42,
            "title": "部署说明",
            "slug": "deploy",
            "body": "# 部署\n\n非默认团队 webhook 更新。",
            "book": {"name": "工程", "namespace": "other/eng"},
            "user_id": 1,
        }

    async def get_repo(self, repo_id):
        assert repo_id == 7
        return {"namespace": "other/eng"}


async def _ok_push(*args, **kwargs):
    return {"pushed": False}


class _PushNotifier:
    def __init__(self):
        self.diff_ids = []
        self.pushed_ids = []

    def should_enable(self):
        return True

    def get_diff(self, doc_id, current_commit, doc_path):
        self.diff_ids.append(doc_id)
        return "diff content with enough text", False

    def pre_check(self, diff, is_first_push=False):
        return False, ""

    async def agent_should_push(self, doc_info, content, is_first_push=False):
        return True, {"highlights": ["更新"], "reason": ""}

    async def notify_subscribers(self, doc_info, summary):
        return None

    def mark_pushed(self, doc_id, commit):
        self.pushed_ids.append(doc_id)


def test_webhook_get_client_for_team_is_backward_compatible(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    handler = _handler(tmp_path, store)

    assert handler._get_client_for_team("other") is None


def test_webhook_get_client_for_team_does_not_swallow_internal_type_error(tmp_path):
    calls = []

    def get_client(team_id="default"):
        calls.append(team_id)
        raise TypeError("client factory misconfigured")

    handler = WebhookHandler(
        docs_dir=tmp_path / "yuque_docs",
        data_dir=tmp_path,
        get_client=get_client,
        rag=None,
        config={"git_enabled": False},
    )

    with pytest.raises(TypeError, match="misconfigured"):
        handler._get_client_for_team("other")

    assert calls == ["other"]


def test_webhook_get_client_for_team_supports_keyword_only_callback(tmp_path):
    calls = []

    def get_client(*, team_id="default"):
        calls.append(team_id)
        return team_id

    handler = WebhookHandler(
        docs_dir=tmp_path / "yuque_docs",
        data_dir=tmp_path,
        get_client=get_client,
        rag=None,
        config={"git_enabled": False},
    )

    assert handler._get_client_for_team("other") == "other"
    assert calls == ["other"]


def test_webhook_get_client_for_team_supports_kwargs_callback(tmp_path):
    calls = []

    def get_client(**kwargs):
        calls.append(kwargs)
        return kwargs.get("team_id")

    handler = WebhookHandler(
        docs_dir=tmp_path / "yuque_docs",
        data_dir=tmp_path,
        get_client=get_client,
        rag=None,
        config={"git_enabled": False},
    )

    assert handler._get_client_for_team("other") == "other"
    assert calls == [{"team_id": "other"}]


def test_webhook_get_client_for_team_does_not_bind_team_id_to_unrelated_parameter(tmp_path):
    calls = []

    def get_client(config=None):
        calls.append(config)
        return "legacy"

    handler = WebhookHandler(
        docs_dir=tmp_path / "yuque_docs",
        data_dir=tmp_path,
        get_client=get_client,
        rag=None,
        config={"git_enabled": False},
    )

    assert handler._get_client_for_team("other") == "legacy"
    assert calls == [None]


async def _run_doc_change(handler):
    return await handler._handle_doc_change(
        {
            "data": {
                "action_type": "update",
                "id": 42,
                "slug": "deploy",
                "book": {"id": 7, "name": "工程", "slug": "eng"},
            }
        }
    )


def test_webhook_doc_change_uses_team_specific_client(tmp_path):
    import asyncio

    docs_dir = tmp_path / "yuque_docs"
    docs_dir.mkdir()
    (docs_dir / ".repos.json").write_text(
        '[{"id":7,"team_id":"other","team_name":"Other","name":"工程","namespace":"other/eng"}]',
        encoding="utf-8",
    )
    requested_team_ids = []

    def get_client(team_id="default"):
        requested_team_ids.append(team_id)
        return _FakeYuqueClient()

    handler = WebhookHandler(
        docs_dir=docs_dir,
        data_dir=tmp_path,
        get_client=get_client,
        rag=None,
        config={"git_enabled": False},
        chunk_store=ChunkStore(tmp_path / "chunks.db"),
    )
    handler._update_doc_index = lambda *args, **kwargs: None
    handler._update_rag = lambda *args, **kwargs: None
    handler._update_chunk_index = lambda *args, **kwargs: None
    handler._git_commit = lambda *args, **kwargs: None
    handler._handle_push = _ok_push

    result = asyncio.run(_run_doc_change(handler))

    assert result["status"] == "ok"
    assert requested_team_ids[0] == "other"
    assert result["path"] == "other/工程/部署说明.md"
    assert (docs_dir / "other" / "工程" / "部署说明.md").exists()


def test_webhook_push_state_is_team_scoped(tmp_path):
    import asyncio

    push_notifier = _PushNotifier()
    handler = WebhookHandler(
        docs_dir=tmp_path / "yuque_docs",
        data_dir=tmp_path,
        get_client=lambda: None,
        rag=None,
        config={"git_enabled": False},
        push_notifier=push_notifier,
    )
    handler.docs_dir.mkdir()

    async def run_pushes():
        await handler._handle_push(
            42,
            "a" * 40,
            "工程/默认.md",
            {"title": "默认", "team_id": "default", "book": {"name": "工程"}},
        )
        await handler._handle_push(
            42,
            "b" * 40,
            "other/工程/其他.md",
            {"title": "其他", "team_id": "other", "book": {"name": "工程"}},
        )

    asyncio.run(run_pushes())

    assert push_notifier.diff_ids == ["42", "other:42"]
    assert push_notifier.pushed_ids == ["42", "other:42"]


def test_webhook_updates_and_deletes_chunk_index(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    handler = _handler(tmp_path, store)
    repo_dir = handler.docs_dir / "工程"
    repo_dir.mkdir()
    doc_file = repo_dir / "部署.md"
    doc_file.write_text(
        """---
id: 42
title: 部署说明
team_id: nova
team_name: NOVA
book_name: 工程
---

Webhook 增量 chunk 更新。
""",
        encoding="utf-8",
    )

    handler._update_chunk_index(42, "工程/部署.md")
    assert store.get_document_chunks("nova:42")

    handler._delete_chunk_index(42, "nova")
    assert store.get_document_chunks("nova:42") == []


def test_webhook_delete_chunk_uses_team_scoped_document_id(tmp_path):
    store = ChunkStore(tmp_path / "chunks.db")
    store.save_document_chunks(
        "99",
        split_markdown(
            "99",
            "默认团队待保留内容",
            title="默认删除测试",
            team_id="default",
            size=220,
            overlap=40,
        ),
    )
    store.save_document_chunks(
        "other:99",
        split_markdown(
            "other:99",
            "其他团队待删除内容",
            title="其他删除测试",
            team_id="other",
            team_name="Other",
            size=220,
            overlap=40,
        ),
    )
    handler = _handler(tmp_path, store)

    handler._delete_chunk_index(99, "other")

    assert store.get_document_chunks("99") != []
    assert store.get_document_chunks("other:99") == []


def test_webhook_delete_scope_can_infer_single_indexed_team(tmp_path):
    handler = _handler(tmp_path, ChunkStore(tmp_path / "chunks.db"))
    with DocIndex(str(tmp_path / "doc_index.db")) as index:
        index.add_doc(
            {
                "yuque_id": 99,
                "title": "其他团队文档",
                "team_id": "other",
                "team_name": "Other",
                "file_path": "other/工程/删除.md",
            }
        )

    team_info, error = handler._resolve_delete_team_info(
        doc_id=99,
        team_info={"team_id": "default", "team_name": "NOVA"},
        team_resolved=False,
    )

    assert error is None
    assert team_info == {"team_id": "other", "team_name": "Other"}


def test_webhook_delete_scope_refuses_ambiguous_unknown_team(tmp_path):
    handler = _handler(tmp_path, ChunkStore(tmp_path / "chunks.db"))
    with DocIndex(str(tmp_path / "doc_index.db")) as index:
        index.add_doc(
            {
                "yuque_id": 99,
                "title": "默认团队文档",
                "team_id": "default",
                "team_name": "NOVA",
                "file_path": "工程/删除.md",
            }
        )
        index.add_doc(
            {
                "yuque_id": 99,
                "title": "其他团队文档",
                "team_id": "other",
                "team_name": "Other",
                "file_path": "other/工程/删除.md",
            }
        )

    team_info, error = handler._resolve_delete_team_info(
        doc_id=99,
        team_info={"team_id": "default", "team_name": "NOVA"},
        team_resolved=False,
    )

    assert team_info is None
    assert "无法确认删除事件所属团队" in error


def test_webhook_delete_scope_trusts_resolved_default_team(tmp_path):
    handler = _handler(tmp_path, ChunkStore(tmp_path / "chunks.db"))
    team_info, error = handler._resolve_delete_team_info(
        doc_id=99,
        team_info={"team_id": "default", "team_name": "NOVA"},
        team_resolved=True,
    )

    assert error is None
    assert team_info == {"team_id": "default", "team_name": "NOVA"}
