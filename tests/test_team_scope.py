from novabot.models import RetrievalScope, Team
import pytest

from novabot.team import TeamRegistry, normalize_yuque_base_url


class _DiscoveryClient:
    users_by_token = {}

    def __init__(self, token, base_url):
        self.token = token
        self.base_url = base_url
        self.closed = False

    async def get_user(self):
        user = self.users_by_token[self.token]
        if isinstance(user, Exception):
            raise user
        return user

    async def close(self):
        self.closed = True


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


def test_team_registry_accepts_token_only_entries_for_discovery():
    registry = TeamRegistry(
        {
            "yuque_base_url": "https://nova.yuque.com/",
            "yuque_teams": [
                {"yuque_token": "token-a", "description": "新手知识库"},
                "token-b",
                {"team_id": "manual", "yuque_token": "token-c"},
            ],
        }
    )

    assert registry.pending_discovery_count == 3
    assert registry.get("manual").team_id == "manual"
    assert registry.get("manual").name == "manual"
    assert registry.get("manual").yuque_base_url == "https://nova.yuque.com/api/v2"


@pytest.mark.asyncio
async def test_team_registry_discovers_group_and_user_tokens():
    _DiscoveryClient.users_by_token = {
        "group-token": {"type": "Group", "id": 123, "name": "NOVA", "login": "nova"},
        "user-token": {"type": "User", "id": 456, "name": "Alice", "login": "alice"},
    }
    registry = TeamRegistry(
        {
            "yuque_teams": [
                {"yuque_token": "group-token", "description": "团队知识库"},
                {"yuque_token": "user-token"},
            ],
        }
    )

    await registry.discover_pending(client_factory=_DiscoveryClient)

    assert registry.pending_discovery_count == 0
    assert registry.get("group_123").name == "NOVA"
    assert registry.get("group_123").description == "团队知识库"
    assert registry.get("user_456").name == "Alice"


@pytest.mark.asyncio
async def test_team_registry_keeps_explicit_team_id_when_discovering_name():
    _DiscoveryClient.users_by_token = {
        "manual-token": {"type": "Group", "id": 123, "name": "API Name", "login": "api"},
    }
    registry = TeamRegistry({"yuque_teams": [{"team_id": "manual", "yuque_token": "manual-token"}]})

    await registry.discover_pending(client_factory=_DiscoveryClient)

    assert registry.get("manual").name == "API Name"
    assert registry.get("group_123") is None


@pytest.mark.asyncio
async def test_team_registry_skips_duplicate_discovered_team_ids():
    _DiscoveryClient.users_by_token = {
        "first": {"type": "Group", "id": 123, "name": "First", "login": "first"},
        "second": {"type": "Group", "id": 123, "name": "Second", "login": "second"},
    }
    registry = TeamRegistry({"yuque_teams": ["first", "second"]})

    await registry.discover_pending(client_factory=_DiscoveryClient)

    assert registry.get("group_123").name == "First"
    assert any("重复" in error for error in registry.discovery_errors)


@pytest.mark.asyncio
async def test_team_registry_discovery_failure_does_not_block_other_teams():
    _DiscoveryClient.users_by_token = {
        "bad": RuntimeError("boom"),
        "good": {"type": "Group", "id": 123, "name": "Good", "login": "good"},
    }
    registry = TeamRegistry({"yuque_teams": ["bad", "good"]})

    await registry.discover_pending(client_factory=_DiscoveryClient)

    assert registry.get("group_123").name == "Good"
    assert any("RuntimeError" in error for error in registry.discovery_errors)


def test_normalize_yuque_base_url_accepts_api_and_web_urls():
    assert normalize_yuque_base_url("https://www.yuque.com/api/v2") == "https://www.yuque.com/api/v2"
    assert normalize_yuque_base_url("https://nova.yuque.com/") == "https://nova.yuque.com/api/v2"
    assert normalize_yuque_base_url("https://nova.yuque.com/api") == "https://nova.yuque.com/api/v2"


def test_team_registry_prefers_entry_level_base_url():
    registry = TeamRegistry(
        {
            "yuque_base_url": "https://global.yuque.com/",
            "yuque_teams": [
                {
                    "team_id": "nova",
                    "name": "NOVA",
                    "yuque_token": "token",
                    "yuque_base_url": "https://entry.yuque.com/",
                }
            ],
        }
    )

    assert registry.get("nova").yuque_base_url == "https://entry.yuque.com/api/v2"


def test_team_registry_rejects_unsafe_team_id():
    registry = TeamRegistry(
        {
            "yuque_teams": [
                {"team_id": "../archive", "name": "Bad", "yuque_token": "token"},
                {"team_id": ".git", "name": "Git", "yuque_token": "token"},
                {"team_id": ".hidden", "name": "Hidden", "yuque_token": "token"},
                {"team_id": "safe-team", "name": "Safe", "yuque_token": "token"},
            ],
        }
    )

    assert registry.get("../archive") is None
    assert registry.get(".git") is None
    assert registry.get(".hidden") is None
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
