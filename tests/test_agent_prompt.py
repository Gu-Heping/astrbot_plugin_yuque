from __future__ import annotations

from novabot.agent import NovaBotAgent
from novabot.team import TeamRegistry


class _Plugin:
    def __init__(self, config):
        self.config = config
        self.team_registry = TeamRegistry(config)


def test_agent_prompt_includes_team_scope_guidance_and_evidence_boundary():
    agent = NovaBotAgent(
        _Plugin(
            {
                "yuque_token": "",
                "yuque_teams": [
                    {
                        "team_id": "research",
                        "name": "Research",
                        "description": "科研论文和实验记录",
                        "yuque_token": "token",
                    }
                ],
            }
        )
    )

    prompt = agent._build_system_prompt(
        {
            "bound": False,
            "platform_id": "u1",
            "sender_name": "Alice",
        }
    )

    assert "【可检索团队范围】" in prompt
    assert "Research (team_id=research)：科研论文和实验记录" in prompt
    assert "先缩小检索范围" in prompt
    assert "【知识事实问答的 Evidence-first 规则】" in prompt
    assert "证据工具包括：search_knowledge_base、grep_local_docs、read_doc、get_doc_details、parse_yuque_url" in prompt
    assert "只有 Grounding Evidence 中的 [E#] 片段可作为事实依据" in prompt
    assert "普通聊天不需要伪造来源" in prompt
    assert "set_preference、profile_view、subscribe、unsubscribe" in prompt
    assert "不要为了这些动作强行先查知识库" in prompt
