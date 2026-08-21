from novabot.models import RetrievalScope, Team
from novabot.team import TeamRegistry


def test_team_registry_keeps_legacy_default_team():
    registry = TeamRegistry(
        {
            "yuque_token": "legacy-token",
            "yuque_base_url": "https://example.com/api/v2",
        }
    )
    team = registry.get()

    assert team == Team.default(
        yuque_token="legacy-token",
        yuque_base_url="https://example.com/api/v2",
    )


def test_team_registry_loads_json_teams_and_disables_empty_legacy_default():
    registry = TeamRegistry(
        {
            "yuque_token": "",
            "yuque_base_url": "https://fallback.example/api/v2",
            "yuque_teams": """
            [
              {
                "team_id": "nova",
                "name": "NOVA",
                "description": "社团知识库",
                "yuque_token": "nova-token"
              },
              {
                "team_id": "off",
                "name": "Disabled",
                "yuque_token": "off-token",
                "enabled": "false"
              }
            ]
            """,
        }
    )

    enabled = registry.list_enabled()

    assert [team.team_id for team in enabled] == ["nova"]
    assert registry.get("nova").yuque_base_url == "https://fallback.example/api/v2"
    assert registry.get("default").enabled is False
    assert registry.get("off").enabled is False
    assert "NOVA (team_id=nova)：社团知识库" in registry.describe_for_agent()
    assert "Disabled" not in registry.describe_for_agent()


def test_team_registry_accepts_dict_wrapper_config():
    registry = TeamRegistry(
        {
            "yuque_teams": {
                "teams": [
                    {
                        "id": "research",
                        "name": "Research",
                        "token": "research-token",
                        "enabled": "yes",
                    }
                ]
            }
        }
    )

    assert [team.team_id for team in registry.list_enabled()] == ["research"]
    assert registry.get("research").yuque_token == "research-token"


def test_team_registry_rejects_unsafe_team_id():
    registry = TeamRegistry(
        {
            "yuque_teams": [
                {"team_id": "../archive", "name": "Bad", "yuque_token": "token"},
                {"team_id": "safe-team", "name": "Safe", "yuque_token": "token"},
            ],
        }
    )

    assert registry.get("../archive") is None
    assert registry.get("safe-team").name == "Safe"


def test_retrieval_scope_composes_team_repo_path_author_time_keyword():
    scope = RetrievalScope.from_dict(
        {
            "team_id": "nova",
            "repository": "AI",
            "path": "指南/入门",
            "author": "Alice",
            "updated_after": "2026-01-01",
            "keyword": "检索",
        }
    )
    doc = {
        "team_id": "nova",
        "book_name": "AI 学习",
        "file_path": "AI 学习/指南/入门/README.md",
        "author": "Alice Zhang",
        "updated_at": "2026-03-01",
        "title": "检索指南",
    }

    assert scope.matches_doc(doc)
    assert not scope.matches_doc({**doc, "team_id": "other"})
    assert not scope.matches_doc({**doc, "file_path": "AI 学习/归档/README.md"})
    assert not scope.matches_doc({**doc, "updated_at": "2025-12-31"})
    assert not scope.matches_doc({**doc, "updated_at": ""})


def test_retrieval_scope_time_filter_requires_updated_at():
    scope = RetrievalScope.from_dict({"updated_after": "2026-01-01"})

    assert scope.matches_doc({"title": "有时间", "updated_at": "2026-02-01"})
    assert not scope.matches_doc({"title": "无时间"})
