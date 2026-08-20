"""
NovaBot 同步后流程编排
用于从 main.py 中拆分后台同步后处理逻辑，降低入口文件复杂度。
"""

import asyncio
import ast
import subprocess
from pathlib import Path
from typing import Any, Callable

from astrbot.api import logger

from .chunk_indexer import rebuild_chunk_index_from_sync
from .git_ops import GitOps
from .models import DEFAULT_TEAM_ID, Team
from .sync_coordinator import run_multi_team_sync, syncable_teams


def select_member_sync_team(
    teams: list[Team],
    requested_team_id: str = "",
) -> tuple[Team | None, str | None]:
    """Select which configured team should provide /sync members data."""

    sync_teams = syncable_teams(teams)
    if not sync_teams:
        return None, "❌ 未配置语雀 Token"

    requested = requested_team_id.strip()
    if requested:
        for team in sync_teams:
            if team.team_id == requested:
                return team, None
        return None, f"❌ 未找到可同步团队: {requested}"

    for team in sync_teams:
        if team.team_id == DEFAULT_TEAM_ID:
            return team, None
    return sync_teams[0], None


def select_sync_teams(
    teams: list[Team],
    requested_team_id: str = "",
) -> tuple[list[Team], str | None]:
    """Select teams for a background knowledge sync."""

    sync_teams = syncable_teams(teams)
    if not sync_teams:
        return [], "❌ 未配置语雀 Token"

    requested = requested_team_id.strip()
    if not requested:
        return sync_teams, None

    for team in sync_teams:
        if team.team_id == requested:
            return [team], None
    return [], f"❌ 未找到可同步团队: {requested}"


async def sync_team_members(*, client, storage) -> str:
    """Synchronize Yuque group members into NovaBot storage."""

    user_info = await client.get_user()
    if user_info.get("type") != "Group":
        return "⚠️ 非团队 Token，跳过成员同步"

    group_id = user_info.get("id")
    members_raw = await client.get_group_members(group_id)

    members = {}
    for item in members_raw:
        user = item.get("user", {})
        uid = user.get("id") or item.get("user_id")
        if uid:
            members[str(uid)] = {
                "name": user.get("name", ""),
                "login": user.get("login", ""),
            }

    if not members:
        return "⚠️ 未获取到成员，请检查 Token 权限"

    storage.save_members(members)
    return f"✅ 团队成员同步完成\n共 {len(members)} 人\n使用 /bind <用户名> 绑定账号"


async def run_background_sync_pipeline(
    *,
    team_registry,
    storage,
    docs_dir: Path,
    rag,
    collaboration_manager,
    trajectory_manager,
    update_collaboration: Callable[[], None],
    init_trajectories: Callable[[], None],
    chunk_store=None,
    yuque_base_url: str = "https://www.yuque.com/api/v2",
    chunk_size: int = 1200,
    chunk_overlap: int = 180,
    git_enabled: bool = True,
    requested_team_id: str = "",
    sync_runner=run_multi_team_sync,
    post_sync_runner=None,
    commit_runner=None,
) -> dict:
    """Run multi-team sync plus all post-sync side effects."""

    sync_teams, error = select_sync_teams(
        team_registry.list_enabled(),
        requested_team_id=requested_team_id,
    )
    if error:
        logger.error(f"[Sync] {error}")
        return {
            "teams_count": 0,
            "result": {"docs": 0, "removed": 0, "errors": 1},
            "team_states": {},
            "repos_info": [],
        }

    sync_summary = await sync_runner(
        teams=sync_teams,
        storage=storage,
        docs_dir=docs_dir,
        members=storage.load_members(),
    )
    total_result = sync_summary["result"]

    post_sync_runner = post_sync_runner or run_post_sync_workflow
    await post_sync_runner(
        result=total_result,
        rag=rag,
        docs_dir=docs_dir,
        storage=storage,
        collaboration_manager=collaboration_manager,
        trajectory_manager=trajectory_manager,
        update_collaboration=update_collaboration,
        init_trajectories=init_trajectories,
        chunk_store=chunk_store,
        yuque_base_url=yuque_base_url,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    docs_count = total_result.get("docs", 0)
    removed_count = total_result.get("removed", 0)
    logger.info(
        f"后台同步完成: {len(sync_teams)} 个团队, {docs_count} 篇文档, "
        f"清理 {removed_count} 个孤儿文件"
    )

    commit_runner = commit_runner or commit_sync_changes
    commit_runner(
        docs_dir=docs_dir,
        result=total_result,
        enabled=git_enabled,
    )

    return sync_summary


def refresh_collaboration_artifacts(
    *,
    collaboration_manager,
    trajectory_manager,
    update_collaboration: Callable[[], None],
    init_trajectories: Callable[[], None],
) -> str:
    """Refresh collaboration network and member trajectories for /sync collab."""

    results = []

    if collaboration_manager:
        try:
            update_collaboration()
            stats = collaboration_manager.get_network_stats()
            results.append(f"协作关系: {stats.get('total_collaborations', 0)} 条")
            results.append(f"参与成员: {stats.get('total_members', 0)} 人")
        except Exception as e:
            logger.error(f"[Sync] 更新协作网络失败: {e}", exc_info=True)
            results.append(f"协作网络更新失败: {e}")

    if trajectory_manager:
        try:
            init_trajectories()
            active_members = trajectory_manager.get_all_active_members(days=30)
            results.append(f"成员轨迹: {len(active_members)} 人有活动记录")
        except Exception as e:
            logger.error(f"[Sync] 初始化轨迹失败: {e}", exc_info=True)
            results.append(f"轨迹初始化失败: {e}")

    if not results:
        return "❌ 数据系统未初始化"

    return "✅ 数据初始化完成\n━━━━━━━━━━━━━━━\n" + "\n".join(results)


async def run_post_sync_workflow(
    *,
    result: dict,
    rag,
    docs_dir,
    storage,
    collaboration_manager,
    trajectory_manager,
    update_collaboration: Callable[[], None],
    init_trajectories: Callable[[], None],
    chunk_store=None,
    yuque_base_url: str = "https://www.yuque.com/api/v2",
    chunk_size: int = 1200,
    chunk_overlap: int = 180,
) -> None:
    """执行同步后的索引与衍生数据更新流程。"""
    docs_count = result.get("docs", 0) if result else 0

    if chunk_store:
        try:
            def chunk_progress(current, total):
                state = storage.load_sync_state()
                state["status"] = "chunk_indexing"
                state["chunk_progress"] = {"current": current, "total": total}
                storage.save_sync_state(state)

            chunk_progress(0, docs_count)
            chunk_result = await asyncio.to_thread(
                rebuild_chunk_index_from_sync,
                docs_dir=docs_dir,
                chunk_store=chunk_store,
                yuque_base_url=yuque_base_url,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                progress_callback=chunk_progress,
            )
            logger.info(
                "[ChunkIndex] 完成: "
                f"{chunk_result['documents']} 篇文档, {chunk_result['chunks']} 个 chunks, "
                f"{chunk_result['errors']} 个错误"
            )
        except Exception as e:
            logger.error(f"[ChunkIndex] 索引失败: {e}", exc_info=True)
        finally:
            state = storage.load_sync_state()
            state.pop("status", None)
            state.pop("chunk_progress", None)
            storage.save_sync_state(state)

    # RAG 对齐：即使 docs=0 也会执行，确保清理历史残留向量。
    if rag:
        try:
            def rag_progress(current, total):
                state = storage.load_sync_state()
                state["status"] = "rag_indexing"
                state["rag_progress"] = {"current": current, "total": total}
                storage.save_sync_state(state)

            rag_progress(0, docs_count)
            indexed = await asyncio.to_thread(
                rag.index_from_sync,
                str(docs_dir),
                rag_progress,
            )
            logger.info(f"RAG 索引完成: {indexed} 篇")
        except Exception as e:
            logger.error(f"RAG 索引失败: {e}")
        finally:
            state = storage.load_sync_state()
            state.pop("status", None)
            state.pop("rag_progress", None)
            storage.save_sync_state(state)

    # 文档为空时跳过协作/轨迹初始化，避免写入无意义数据。
    if docs_count <= 0:
        logger.info("[Sync] 本次同步文档为 0，已完成索引清空对齐")
        return

    if collaboration_manager:
        try:
            update_collaboration()
        except Exception as e:
            logger.error(f"[Collaboration] 更新协作网络失败: {e}", exc_info=True)

    if trajectory_manager:
        try:
            init_trajectories()
        except Exception as e:
            logger.error(f"[Trajectory] 初始化轨迹失败: {e}", exc_info=True)


def commit_sync_changes(
    *,
    docs_dir: Path,
    result: dict,
    enabled: bool = True,
    git_factory: Callable[[Path], Any] = GitOps,
    status_runner: Callable[..., Any] = subprocess.run,
) -> str | None:
    """Commit changed synced docs when Git auto-commit is enabled."""

    if not enabled:
        return None

    git = git_factory(Path(docs_dir))
    if not git.is_git_repo() or not git.has_user_identity():
        return None

    try:
        status_result = status_runner(
            ["git", "status", "--porcelain"],
            cwd=docs_dir,
            capture_output=True,
            text=True,
        )
        changed_files = _parse_git_status_files(getattr(status_result, "stdout", ""))
        if not changed_files:
            return None
        return git.add_commit(changed_files, build_sync_commit_message(result))
    except Exception as e:
        logger.warning(f"[Sync] Git commit 失败: {e}")
        return None


def build_sync_commit_message(result: dict) -> str:
    docs_count = result.get("docs", 0) if result else 0
    removed_count = result.get("removed", 0) if result else 0
    commit_msg = f"sync: 同步 {docs_count} 篇文档"
    if removed_count > 0:
        commit_msg += f", 清理 {removed_count} 个文件"
    return commit_msg


def mark_sync_failed(storage) -> None:
    """Clear volatile sync progress fields after a background sync failure."""

    state = storage.load_sync_state()
    state["in_progress"] = False
    state["progress"] = None
    state["team_progress"] = None
    state.pop("status", None)
    state.pop("rag_progress", None)
    state.pop("chunk_progress", None)
    storage.save_sync_state(state)


def _parse_git_status_files(stdout: str) -> list[str]:
    files = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line.strip()
        if " -> " in path:
            old_path, new_path = path.split(" -> ", 1)
            files.extend([_unquote_git_path(old_path), _unquote_git_path(new_path)])
        else:
            files.append(_unquote_git_path(path))
    return files


def _unquote_git_path(path: str) -> str:
    value = path.strip()
    if not (value.startswith('"') and value.endswith('"')):
        return value
    try:
        decoded = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value.strip('"')
    decoded_text = str(decoded)
    try:
        return decoded_text.encode("latin1").decode("utf-8")
    except UnicodeError:
        return decoded_text
