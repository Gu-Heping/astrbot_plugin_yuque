"""Admin command helpers for legacy RAG maintenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class RagLike(Protocol):
    def get_stats(self) -> dict: ...

    def search(self, query: str, k: int = 5) -> list[dict]: ...

    def clear(self) -> bool: ...

    def index_from_sync(self, docs_dir: str) -> int: ...


class SearchLoggerLike(Protocol):
    def log_search(
        self,
        *,
        query: str,
        results_count: int,
        search_type: str,
        user_id: str,
    ) -> None: ...


@dataclass
class RagCommandContext:
    rag: RagLike | None
    docs_dir: Path
    embedding_model: str
    search_logger: SearchLoggerLike


def handle_rag_command(
    context: RagCommandContext,
    *,
    action: str = "",
    query: str = "",
    user_id: str = "",
) -> list[str]:
    """Execute a /rag admin command and return user-facing messages."""
    if not context.rag:
        return ["❌ RAG 未初始化，请配置 embedding_api_key"]

    action_lower = action.lower()
    if action_lower == "status":
        stats = context.rag.get_stats()
        return [
            "📊 RAG 状态\n"
            f"模型: {context.embedding_model}\n"
            f"文档数: {stats.get('docs_count', 0)}"
        ]

    if action_lower == "search" and query:
        results = context.rag.search(query, k=5)
        context.search_logger.log_search(
            query=query,
            results_count=len(results),
            search_type="rag",
            user_id=user_id,
        )
        if not results:
            return [f"未找到相关文档: {query}"]

        lines = [f"🔍 搜索: {query}", "━━━━━━━━━━━━━━━"]
        for i, doc in enumerate(results, 1):
            lines.append(f"{i}. {doc['title']}")
            lines.append(f"   {doc['content'][:80]}...")
        return ["\n".join(lines)]

    if action_lower == "rebuild":
        messages = ["🔄 重建 RAG 索引..."]
        indexed = context.rag.index_from_sync(str(context.docs_dir))
        messages.append(f"✅ 重建完成，索引 {indexed} 篇文档")
        return messages

    return [
        "📚 RAG 检索\n"
        "• /rag status - 状态\n"
        "• /rag search <关键词> - 搜索\n"
        "• /rag rebuild - 重建索引"
    ]
