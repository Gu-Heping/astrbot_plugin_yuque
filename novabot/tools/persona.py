"""
人格偏好相关工具：设置称呼、语气等
"""

from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .base import BaseTool
from ..storage import Storage


@dataclass
class SetPreferenceTool(BaseTool):
    """设置用户人格偏好工具

    当用户说"叫我XX"、"说话活泼点"等时调用
    """

    name: str = "set_preference"
    description: str = "设置用户的人格偏好，如称呼、语气、回复风格等。当用户说'叫我XX'、'说话活泼点'、'不要太啰嗦'等表达偏好时调用。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "preference_type": {
                "type": "string",
                "description": "偏好类型：name（称呼）、tone（语气）、style（回复风格）、formality（正式程度）"
            },
            "value": {
                "type": "string",
                "description": "偏好值。语气可选：温和/活泼/严肃/幽默；回复风格可选：简洁/详细；正式程度可选：轻松/正式；称呼可以是任意字符串"
            }
        },
        "required": ["preference_type", "value"]
    })
    plugin: Any = None

    async def run(self, event: AstrMessageEvent, preference_type: str, value: str) -> str:
        if not self.plugin:
            return "插件未初始化"

        # 获取用户绑定信息
        platform_id = event.get_sender_id()
        binding = self.plugin.storage.get_binding(platform_id)

        if not binding:
            return "请先绑定账号后再设置偏好。使用 /bind <用户名> 绑定。"

        yuque_id = binding.get("yuque_id")
        if not yuque_id:
            return "绑定信息异常，请重新绑定"

        # 验证偏好类型
        valid_types = {
            "name": "称呼",
            "tone": "语气",
            "style": "回复风格",
            "formality": "正式程度",
        }

        if preference_type not in valid_types:
            return f"未知的偏好类型：{preference_type}。可选：{', '.join(valid_types.keys())}"

        # 验证偏好值（使用 Storage 常量）
        valid_values = {
            "tone": Storage.VALID_TONES,
            "style": Storage.VALID_STYLES,
            "formality": Storage.VALID_FORMALITIES,
        }

        if preference_type in valid_values:
            if value not in valid_values[preference_type]:
                return f"无效的{valid_types[preference_type]}值：{value}。可选：{', '.join(valid_values[preference_type])}"

        # 更新偏好
        success = self.plugin.storage.update_preference(yuque_id, preference_type, value)
        if not success:
            return f"设置失败，请检查偏好类型和值是否正确"

        logger.info(f"[SetPreference] 用户 {binding.get('yuque_name')} 设置 {preference_type} = {value}")

        # 返回简洁结果，让 Agent 生成友好的确认
        type_name = valid_types[preference_type]
        return f"已更新{type_name}为「{value}」"