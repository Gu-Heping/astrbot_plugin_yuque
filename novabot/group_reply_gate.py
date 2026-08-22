"""
NovaBot 群聊智能旁听守门
判断群聊非显式触发消息是否值得回复
"""

import json
import re
from typing import TYPE_CHECKING, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .chat_participant import extract_chat_participant, format_history_item
from .llm_utils import call_llm, sanitize_user_input
from .token_monitor import FEATURE_GROUP_GATE

if TYPE_CHECKING:
    from ..main import NovaBotPlugin

# 水群短词（去空白、小写后精确匹配）
_NOISE_EXACT = frozenset(
    {
        "收到",
        "好的",
        "好",
        "ok",
        "okay",
        "嗯",
        "哦",
        "啊",
        "哈哈",
        "哈哈哈",
        "呵呵",
        "+1",
        "666",
        "赞",
        "顶",
        "谢谢",
        "感谢",
        "明白",
        "了解",
        "知道了",
        "行",
        "可以",
        "没问题",
    }
)

# 纯标点/表情/数字
_NOISE_PATTERN = re.compile(r"^[\s\W\d]+$", re.UNICODE)

_GATE_SYSTEM_PROMPT = """你是 NovaBot 群聊旁听守门员，服务于 NOVA 社团。

判断当前群消息是否需要 NovaBot（社团知识库智能助手）回复。

【应该回复 should_reply=true】
- 明确在向 NovaBot 或社团知识库提问、求助
- 学习、文档、教程、知识检索相关的问题
- 延续上一轮对 NovaBot 的对话（结合上下文）

【不应回复 should_reply=false】
- 成员之间的闲聊，未涉及助手或知识库
- 表情包、无意义短句、附和（收到、好的、哈哈等）
- 明显不是在找助手，只是群内日常交流
- 不确定是否需要介入时

原则：不确定则 should_reply=false，避免刷屏。

输出 JSON：{"should_reply": true/false, "reason": "简短原因"}"""


async def should_reply(
    event: AstrMessageEvent,
    msg: str,
    plugin: "NovaBotPlugin",
) -> tuple[bool, str]:
    """判断群聊消息是否应由 NovaBot 回复（仅用于 smart 模式候选消息）

    Returns:
        (should_reply, reason)
    """
    text = (msg or "").strip()
    if not text:
        return False, "empty_message"

    if len(text) < 4:
        return False, "too_short"

    normalized = text.lower().strip()
    if normalized in _NOISE_EXACT:
        return False, "noise_exact"

    if _NOISE_PATTERN.match(text):
        return False, "noise_pattern"

    try:
        umo = event.unified_msg_origin
        prov_id = await plugin.context.get_current_chat_provider_id(umo)
        if not prov_id:
            return False, "no_provider"

        prov = plugin.context.get_provider_by_id(prov_id)
        if not prov:
            return False, "no_provider"

        participant = extract_chat_participant(event)
        sender_name = participant.safe_display_name
        safe_msg = sanitize_user_input(text, max_length=500)
        history_text = await _format_recent_history(plugin, umo)

        prompt_parts = [
            f"发送者: {sender_name}",
            f"发送者平台 ID: {participant.safe_platform_id or '未知'}",
            f"群聊 ID: {participant.safe_group_id or '未知'}",
            f"当前消息: {safe_msg}",
        ]
        if history_text:
            prompt_parts.append(f"最近对话:\n{history_text}")
        prompt = "\n".join(prompt_parts)

        result = await call_llm(
            provider=prov,
            prompt=prompt,
            system_prompt=_GATE_SYSTEM_PROMPT,
            require_json=True,
            token_monitor=plugin.token_monitor,
            feature=FEATURE_GROUP_GATE,
        )

        should = bool(result.get("should_reply", False))
        reason = str(result.get("reason", "") or ("llm_yes" if should else "llm_no"))
        logger.info(f"[GroupGate] should_reply={should}, reason={reason}")
        return should, reason

    except Exception as e:
        logger.warning(f"[GroupGate] 守门失败，静默跳过: {e}")
        return False, "gate_error"


async def _format_recent_history(plugin: "NovaBotPlugin", umo: str) -> Optional[str]:
    """获取最近 1 轮对话文本（与 Agent 同源：conversation_manager）"""
    try:
        conv_mgr = plugin.context.conversation_manager
        if not conv_mgr:
            return None

        curr_cid = await conv_mgr.get_curr_conversation_id(umo)
        if not curr_cid:
            return None

        conversation = await conv_mgr.get_conversation(umo, curr_cid)
        if not conversation or not conversation.history:
            return None

        history_data = json.loads(conversation.history)
        if not isinstance(history_data, list):
            return None

        recent = history_data[-2:]  # 最近 1 轮
        lines = []
        for item in recent:
            if not isinstance(item, dict):
                continue
            formatted = format_history_item(item, is_group=True)
            if formatted:
                lines.append(formatted[:200])
        return "\n".join(lines) if lines else None

    except Exception as e:
        logger.debug(f"[GroupGate] 读取对话历史失败: {e}")
        return None
