"""Community-derived artifacts built from synced document metadata."""

from __future__ import annotations

from datetime import datetime

from astrbot.api import logger


def update_collaboration_network_from_docs(
    *,
    doc_index,
    collaboration_manager,
    team_id: str | None = None,
) -> dict:
    """Build same-repository collaboration edges from document metadata."""

    if not collaboration_manager:
        return {"repos": 0, "relations": 0}

    if not doc_index:
        logger.warning("[Collaboration] 文档索引未初始化")
        return {"repos": 0, "relations": 0}

    try:
        docs = _load_docs(doc_index, team_id=team_id)
    except Exception as e:
        logger.error(f"[Collaboration] 获取文档列表失败: {e}")
        return {"repos": 0, "relations": 0}

    repo_contributors: dict[tuple[str, str], set[str]] = {}
    for doc in docs:
        book_name = doc.get("book_name", "")
        creator_id = doc.get("creator_id")
        if not book_name or not creator_id:
            continue
        doc_team_id = str(doc.get("team_id") or "default")
        repo_contributors.setdefault((doc_team_id, book_name), set()).add(str(creator_id))

    total_repos = 0
    total_relations = 0
    for (repo_team_id, book_name), contributors in repo_contributors.items():
        if len(contributors) < 2:
            continue

        repo_key = book_name if repo_team_id == "default" else f"{repo_team_id}/{book_name}"
        collaboration_manager.add_repo_contributors(repo_key, list(contributors))
        total_repos += 1
        n = len(contributors)
        total_relations += n * (n - 1) // 2

    logger.info(
        f"[Collaboration] 协作网络更新完成: "
        f"{total_repos} 个知识库, {total_relations} 条关系"
    )
    return {"repos": total_repos, "relations": total_relations}


def init_member_trajectories_from_docs(
    *,
    doc_index,
    trajectory_manager,
    team_id: str | None = None,
) -> dict:
    """Create member trajectory events from document publish/update metadata."""

    if not trajectory_manager:
        return {"members": 0, "events": 0}

    if not doc_index:
        logger.warning("[Trajectory] 文档索引未初始化")
        return {"members": 0, "events": 0}

    try:
        docs = _load_docs(doc_index, team_id=team_id)
    except Exception as e:
        logger.error(f"[Trajectory] 获取文档列表失败: {e}")
        return {"members": 0, "events": 0}

    member_docs: dict[str, list[dict]] = {}
    for doc in docs:
        creator_id = doc.get("creator_id")
        if not creator_id:
            continue
        member_docs.setdefault(str(creator_id), []).append(doc)

    total_events = 0
    for creator_id, doc_list in member_docs.items():
        doc_list.sort(key=lambda d: d.get("updated_at", ""), reverse=True)

        for doc in doc_list[:20]:
            event_type, event_time = _doc_trajectory_event(doc)
            trajectory_manager.record_event(
                member_id=creator_id,
                event_type=event_type,
                title=doc.get("title", ""),
                description=f"知识库: {doc.get('book_name', '')}",
                related_id=str(doc.get("yuque_id", "")),
                timestamp=event_time,
            )
            total_events += 1

    logger.info(
        f"[Trajectory] 成员轨迹初始化完成: "
        f"{len(member_docs)} 个成员, {total_events} 条轨迹"
    )
    return {"members": len(member_docs), "events": total_events}


def _doc_trajectory_event(doc: dict) -> tuple[str, str]:
    created_at = doc.get("created_at", "")
    updated_at = doc.get("updated_at", "")

    if not (created_at and updated_at):
        return "publish_doc", updated_at

    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return "publish_doc", updated_at

    if abs((updated - created).total_seconds()) < 3600:
        return "publish_doc", created_at
    return "update_doc", updated_at


def _load_docs(doc_index, *, team_id: str | None = None) -> list[dict]:
    if not team_id:
        return doc_index.get_all_docs()
    try:
        return doc_index.search(team_id=team_id, limit=10000)
    except TypeError:
        return [
            doc
            for doc in doc_index.get_all_docs()
            if str(doc.get("team_id") or "default") == team_id
        ]
