from pathlib import Path
import re


def test_subscriptions_command_is_known_without_slash_for_lark():
    source = Path("main.py").read_text(encoding="utf-8")
    match = re.search(r"known_commands = \[(.*?)\]", source, re.S)

    assert match is not None
    assert '"subscriptions"' in match.group(1)
