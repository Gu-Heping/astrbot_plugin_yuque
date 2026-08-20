from novabot.chunking import split_markdown
from novabot.evidence import format_citations, format_evidence_block, select_grounding_evidence
from novabot.models import RetrievalResult


def _result(document_id: str, content: str, *, reliable: bool, score: float = 0.9):
    chunk = split_markdown(
        document_id,
        content,
        title=f"文档{document_id}",
        team_id="nova",
        team_name="NOVA",
        repository="工程",
        file_path=f"工程/{document_id}.md",
        source_url=f"https://example.com/{document_id}",
        size=220,
        overlap=40,
    )[0]
    return RetrievalResult(chunk=chunk, score=score, methods=("keyword",), reliable=reliable)


def test_select_grounding_evidence_keeps_only_reliable_and_dedupes():
    results = [
        _result("1", "可靠内容", reliable=True, score=0.8),
        _result("2", "不可靠内容", reliable=False, score=1.0),
        _result("1", "可靠内容", reliable=True, score=0.7),
    ]

    evidence = select_grounding_evidence(results)

    assert [item.evidence_id for item in evidence] == ["E1"]
    assert evidence[0].document_id == "1"
    assert "可靠内容" in evidence[0].content


def test_format_evidence_block_for_no_reliable_evidence_blocks_fact_answer():
    block = format_evidence_block([])

    assert "未找到 reliable=true" in block
    assert "不要使用候选片段编造" in block
    assert format_citations([]) == "参考来源：无 reliable evidence"


def test_format_evidence_block_includes_citation_ids_and_sources():
    evidence = select_grounding_evidence([_result("1", "可靠内容", reliable=True)])
    block = format_evidence_block(evidence)
    citations = format_citations(evidence)

    assert "[E1]" in block
    assert "可靠内容" in block
    assert "https://example.com/1" in citations
