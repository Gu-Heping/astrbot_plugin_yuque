"""Evidence selection for NovaBot knowledge-fact answers.

Search can return many candidates. Only reliable, deduplicated excerpts should
be promoted into the evidence set that a factual answer may cite.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import RetrievalResult


@dataclass(frozen=True)
class EvidenceExcerpt:
    evidence_id: str
    document_id: str
    title: str
    content: str
    source_url: str = ""
    file_path: str = ""
    team_id: str = ""
    team_name: str = ""
    repository: str = ""
    author: str = ""
    score: float = 0.0
    evidence_type: str = "chunk"


def evidence_from_retrieval(result: RetrievalResult, evidence_id: str) -> EvidenceExcerpt:
    chunk = result.chunk
    return EvidenceExcerpt(
        evidence_id=evidence_id,
        document_id=chunk.document_id,
        title=chunk.title,
        content=chunk.content,
        source_url=chunk.source_url,
        file_path=chunk.file_path,
        team_id=chunk.team_id,
        team_name=chunk.team_name,
        repository=chunk.repository,
        author=chunk.author,
        score=result.score,
    )


def evidence_from_document_content(
    doc: dict,
    content: str,
    evidence_id: str = "E1",
    *,
    score: float = 1.0,
    evidence_type: str = "document",
) -> EvidenceExcerpt:
    """Create citeable evidence from an explicitly read document slice."""

    document_id = str(doc.get("yuque_id") or doc.get("id") or doc.get("file_path") or "")
    return EvidenceExcerpt(
        evidence_id=evidence_id,
        document_id=document_id,
        title=str(doc.get("title") or ""),
        content=content,
        source_url=str(doc.get("url") or ""),
        file_path=str(doc.get("file_path") or ""),
        team_id=str(doc.get("team_id") or ""),
        team_name=str(doc.get("team_name") or ""),
        repository=str(doc.get("book_name") or doc.get("repository") or ""),
        author=str(doc.get("author") or ""),
        score=score,
        evidence_type=evidence_type,
    )


def select_grounding_evidence(
    results: list[RetrievalResult],
    *,
    max_evidence: int = 5,
) -> list[EvidenceExcerpt]:
    """Promote only reliable retrieval results into citeable evidence."""

    selected: list[EvidenceExcerpt] = []
    seen: set[tuple[str, str]] = set()
    reliable = [result for result in results if result.reliable]
    reliable.sort(key=lambda result: result.score, reverse=True)

    for result in reliable:
        chunk = result.chunk
        key = (chunk.document_id, _content_key(chunk.content))
        if key in seen:
            continue
        seen.add(key)
        selected.append(evidence_from_retrieval(result, f"E{len(selected) + 1}"))
        if len(selected) >= max_evidence:
            break
    return selected


def format_evidence_block(evidence: list[EvidenceExcerpt]) -> str:
    if not evidence:
        return (
            "【Grounding Evidence】\n"
            "未找到 reliable=true 的证据片段。知识事实问答应回答“知识库中暂未找到可靠答案”，"
            "不要使用候选片段编造。"
        )

    lines = ["【Grounding Evidence】"]
    for item in evidence:
        lines.append(f"[{item.evidence_id}] 《{item.title or '未知'}》")
        if item.team_name or item.team_id:
            lines.append(f"团队: {item.team_name} ({item.team_id})")
        if item.repository:
            lines.append(f"知识库: {item.repository}")
        if item.author:
            lines.append(f"作者: {item.author}")
        if item.file_path:
            lines.append(f"路径: {item.file_path}")
        if item.source_url:
            lines.append(f"链接: {item.source_url}")
        lines.append(f"score={item.score:.3f}")
        lines.append(item.content[:1200])
        lines.append("")
    lines.append("规则：知识事实回答只能使用上方 [E#] 证据；引用事实时标注对应 [E#]。")
    return "\n".join(lines).rstrip()


def format_citations(evidence: list[EvidenceExcerpt]) -> str:
    if not evidence:
        return "参考来源：无 reliable evidence"
    lines = ["参考来源："]
    for item in evidence:
        source = item.source_url or item.file_path or item.repository or item.document_id
        lines.append(f"- [{item.evidence_id}] 《{item.title or '未知'}》：{source}")
    return "\n".join(lines)


def _content_key(content: str) -> str:
    normalized = " ".join(content.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
