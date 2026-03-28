"""
NovaBot 提示词管理模块
统一管理所有 LLM 提示词
"""

from .profile import PROFILE_PROMPT, DOMAIN_ASSESS_PROMPT
from .knowledge_card import CARD_PROMPT
from .learning_path import PATH_PROMPT, PATH_FALLBACK_PROMPT
from .push import FIRST_PUSH_PROMPT, UPDATE_PUSH_PROMPT
from .learning_gap import GAP_PROMPT, GAP_NO_BINDING_PROMPT, GAP_NO_PROFILE_PROMPT, GAP_NO_TARGET_PROMPT

PROMPTS = {
    "profile": PROFILE_PROMPT,
    "domain_assess": DOMAIN_ASSESS_PROMPT,
    "knowledge_card": CARD_PROMPT,
    "learning_path": PATH_PROMPT,
    "learning_path_fallback": PATH_FALLBACK_PROMPT,
    "push_first": FIRST_PUSH_PROMPT,
    "push_update": UPDATE_PUSH_PROMPT,
    "learning_gap": GAP_PROMPT,
}

__all__ = [
    "PROMPTS",
    "PROFILE_PROMPT",
    "DOMAIN_ASSESS_PROMPT",
    "CARD_PROMPT",
    "PATH_PROMPT",
    "PATH_FALLBACK_PROMPT",
    "FIRST_PUSH_PROMPT",
    "UPDATE_PUSH_PROMPT",
    "GAP_PROMPT",
    "GAP_NO_BINDING_PROMPT",
    "GAP_NO_PROFILE_PROMPT",
    "GAP_NO_TARGET_PROMPT",
]