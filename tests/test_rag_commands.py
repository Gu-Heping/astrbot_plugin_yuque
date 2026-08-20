from pathlib import Path

from novabot.rag_commands import RagCommandContext, handle_rag_command


class _Rag:
    def __init__(self):
        self.cleared = False
        self.indexed_dir = ""

    def get_stats(self):
        return {"docs_count": 12}

    def search(self, query, k=5):
        assert query == "部署"
        assert k == 5
        return [{"title": "部署说明", "content": "NovaBot 多团队部署说明" * 10}]

    def clear(self):
        self.cleared = True
        return True

    def index_from_sync(self, docs_dir):
        self.indexed_dir = docs_dir
        return 3


class _SearchLogger:
    def __init__(self):
        self.calls = []

    def log_search(self, **kwargs):
        self.calls.append(kwargs)


_MISSING = object()


def _context(rag=_MISSING, docs_dir=Path("docs")):
    logger = _SearchLogger()
    return RagCommandContext(
        rag=_Rag() if rag is _MISSING else rag,
        docs_dir=docs_dir,
        embedding_model="text-embedding-3-small",
        search_logger=logger,
    ), logger


def test_rag_command_reports_uninitialized():
    context, _ = _context(rag=None)

    assert handle_rag_command(context, action="status") == ["❌ RAG 未初始化，请配置 embedding_api_key"]


def test_rag_command_status_formats_stats():
    context, _ = _context()

    text = handle_rag_command(context, action="status")[0]

    assert "📊 RAG 状态" in text
    assert "模型: text-embedding-3-small" in text
    assert "文档数: 12" in text


def test_rag_command_search_logs_and_formats_results():
    context, logger = _context()

    text = handle_rag_command(context, action="search", query="部署", user_id="u1")[0]

    assert "🔍 搜索: 部署" in text
    assert "1. 部署说明" in text
    assert logger.calls == [
        {"query": "部署", "results_count": 1, "search_type": "rag", "user_id": "u1"}
    ]


def test_rag_command_rebuild_indexes_docs_without_preemptive_clear(tmp_path):
    rag = _Rag()
    context, _ = _context(rag=rag, docs_dir=tmp_path)

    messages = handle_rag_command(context, action="rebuild")

    assert messages == ["🔄 重建 RAG 索引...", "✅ 重建完成，索引 3 篇文档"]
    assert rag.cleared is False
    assert rag.indexed_dir == str(tmp_path)


def test_rag_command_rebuild_does_not_call_outer_clear(tmp_path):
    class FailedClearRag(_Rag):
        def clear(self):
            raise AssertionError("outer clear should not run")

    context, _ = _context(rag=FailedClearRag(), docs_dir=tmp_path)

    assert handle_rag_command(context, action="rebuild") == ["🔄 重建 RAG 索引...", "✅ 重建完成，索引 3 篇文档"]
