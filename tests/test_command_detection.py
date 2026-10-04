from pathlib import Path


def test_novabot_help_command_has_common_case_aliases():
    source = Path("main.py").read_text(encoding="utf-8")

    assert 'alias={"Novabot", "NovaBot", "NOVABOT"}' in source
    assert "known_commands =" not in source
    assert "priority=maxsize - 2" in source


def test_non_chat_events_return_before_claim_deduplication_and_queueing():
    source = Path("main.py").read_text(encoding="utf-8")
    route_start = source.index("non_chat_reason = non_chat_event_reason(event)")
    claim_start = source.index("# Multiple live plugin instances", route_start)
    branch = source[route_start:claim_start]

    assert "suppress_default_llm(event)" in branch
    assert "return" in branch
    assert "stop_event" not in branch
    assert "_message_deduplicator" not in branch
    assert "_conversation_queue" not in branch
    assert "agent.handle_message" not in branch
