from __future__ import annotations

import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


astrbot = types.ModuleType("astrbot")
astrbot_api = types.ModuleType("astrbot.api")
astrbot_api_event = types.ModuleType("astrbot.api.event")
astrbot_core_agent_tool = types.ModuleType("astrbot.core.agent.tool")
astrbot_core_agent_message = types.ModuleType("astrbot.core.agent.message")


class _Logger:
    def debug(self, *args, **kwargs): ...
    def info(self, *args, **kwargs): ...
    def warning(self, *args, **kwargs): ...
    def error(self, *args, **kwargs): ...


astrbot_api.logger = _Logger()
astrbot_api.FunctionTool = object
astrbot_api_event.AstrMessageEvent = object
astrbot_core_agent_tool.ToolSet = lambda tools: tools


class _MessageSegment:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


astrbot_core_agent_message.AssistantMessageSegment = _MessageSegment
astrbot_core_agent_message.UserMessageSegment = _MessageSegment
astrbot_core_agent_message.TextPart = _MessageSegment
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", astrbot_api)
sys.modules.setdefault("astrbot.api.event", astrbot_api_event)
sys.modules.setdefault("astrbot.core.agent.tool", astrbot_core_agent_tool)
sys.modules.setdefault("astrbot.core.agent.message", astrbot_core_agent_message)
