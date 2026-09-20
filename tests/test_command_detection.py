from pathlib import Path


def test_novabot_help_command_has_common_case_aliases():
    source = Path("main.py").read_text(encoding="utf-8")

    assert 'alias={"Novabot", "NovaBot", "NOVABOT"}' in source
    assert "known_commands =" not in source
    assert "priority=maxsize - 2" in source
