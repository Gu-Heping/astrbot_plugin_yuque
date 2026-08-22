from __future__ import annotations

from novabot.community_artifacts import (
    init_member_trajectories_from_docs,
    update_collaboration_network_from_docs,
)
from novabot.memory.collaboration_network import CollaborationNetwork


class _DocIndex:
    def __init__(self, docs):
        self.docs = docs

    def get_all_docs(self):
        return list(self.docs)


class _BrokenDocIndex:
    def get_all_docs(self):
        raise RuntimeError("db down")


class _CollaborationManager:
    def __init__(self):
        self.repos = []

    def add_repo_contributors(self, repo_name, contributors):
        self.repos.append((repo_name, sorted(contributors)))


class _BatchCollaborationManager(_CollaborationManager):
    def __init__(self):
        super().__init__()
        self.batches = []

    def add_repositories_contributors(self, repo_contributors):
        self.batches.append(
            {
                repo_name: sorted(contributors)
                for repo_name, contributors in repo_contributors.items()
            }
        )


class _CountingCollaborationNetwork(CollaborationNetwork):
    def __init__(self, data_dir):
        super().__init__(data_dir)
        self.save_count = 0

    def _save_network(self, network):
        self.save_count += 1
        super()._save_network(network)


class _TrajectoryManager:
    def __init__(self):
        self.events = []

    def record_event(self, **kwargs):
        self.events.append(kwargs)


def test_update_collaboration_network_groups_multi_author_repos():
    manager = _CollaborationManager()
    result = update_collaboration_network_from_docs(
        doc_index=_DocIndex(
            [
                {"book_name": "工程", "creator_id": 1},
                {"book_name": "工程", "creator_id": 2},
                {"book_name": "工程", "creator_id": 2},
                {"book_name": "单人", "creator_id": 3},
                {"book_name": "", "creator_id": 4},
            ]
        ),
        collaboration_manager=manager,
    )

    assert manager.repos == [("工程", ["1", "2"])]
    assert result == {"repos": 1, "relations": 1}


def test_update_collaboration_network_uses_batch_manager_when_available():
    manager = _BatchCollaborationManager()
    result = update_collaboration_network_from_docs(
        doc_index=_DocIndex(
            [
                {"book_name": "工程", "creator_id": 1},
                {"book_name": "工程", "creator_id": 2},
                {"book_name": "产品", "creator_id": 2},
                {"book_name": "产品", "creator_id": 3},
            ]
        ),
        collaboration_manager=manager,
    )

    assert manager.repos == []
    assert manager.batches == [{"工程": ["1", "2"], "产品": ["2", "3"]}]
    assert result == {"repos": 2, "relations": 2}


def test_collaboration_network_batches_repo_contributor_writes(tmp_path):
    manager = _CountingCollaborationNetwork(tmp_path)

    manager.add_repositories_contributors(
        {
            "工程": ["1", "2", "3"],
            "产品": ["2", "3", "4"],
        }
    )

    assert manager.save_count == 1
    assert manager.get_network_stats()["total_collaborations"] == 5
    assert manager.get_member_stats("2")["collaborator_count"] == 3


def test_update_collaboration_network_handles_missing_or_broken_inputs():
    assert update_collaboration_network_from_docs(
        doc_index=None,
        collaboration_manager=_CollaborationManager(),
    ) == {"repos": 0, "relations": 0}
    assert update_collaboration_network_from_docs(
        doc_index=_DocIndex([]),
        collaboration_manager=None,
    ) == {"repos": 0, "relations": 0}
    assert update_collaboration_network_from_docs(
        doc_index=_BrokenDocIndex(),
        collaboration_manager=_CollaborationManager(),
    ) == {"repos": 0, "relations": 0}


def test_init_member_trajectories_records_publish_and_update_events():
    manager = _TrajectoryManager()
    result = init_member_trajectories_from_docs(
        doc_index=_DocIndex(
            [
                {
                    "creator_id": 1,
                    "title": "发布",
                    "book_name": "工程",
                    "yuque_id": 10,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:30:00+00:00",
                },
                {
                    "creator_id": 1,
                    "title": "更新",
                    "book_name": "工程",
                    "yuque_id": 11,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                },
                {"creator_id": "", "title": "跳过"},
            ]
        ),
        trajectory_manager=manager,
    )

    assert result == {"members": 1, "events": 2}
    assert [event["event_type"] for event in manager.events] == ["update_doc", "publish_doc"]
    assert manager.events[0]["title"] == "更新"
    assert manager.events[0]["description"] == "知识库: 工程"
    assert manager.events[0]["related_id"] == "11"


def test_init_member_trajectories_limits_each_member_to_recent_twenty_docs():
    manager = _TrajectoryManager()
    docs = [
        {
            "creator_id": "u1",
            "title": f"文档 {i}",
            "book_name": "工程",
            "yuque_id": i,
            "updated_at": f"2026-01-{i:02d}T00:00:00+00:00",
        }
        for i in range(1, 26)
    ]

    result = init_member_trajectories_from_docs(
        doc_index=_DocIndex(docs),
        trajectory_manager=manager,
    )

    assert result == {"members": 1, "events": 20}
    assert manager.events[0]["title"] == "文档 25"
    assert manager.events[-1]["title"] == "文档 6"


def test_init_member_trajectories_handles_missing_or_broken_inputs():
    assert init_member_trajectories_from_docs(
        doc_index=None,
        trajectory_manager=_TrajectoryManager(),
    ) == {"members": 0, "events": 0}
    assert init_member_trajectories_from_docs(
        doc_index=_DocIndex([]),
        trajectory_manager=None,
    ) == {"members": 0, "events": 0}
    assert init_member_trajectories_from_docs(
        doc_index=_BrokenDocIndex(),
        trajectory_manager=_TrajectoryManager(),
    ) == {"members": 0, "events": 0}
