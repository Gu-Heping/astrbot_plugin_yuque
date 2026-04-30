"""
NovaBot 同步后流程编排
用于从 main.py 中拆分后台同步后处理逻辑，降低入口文件复杂度。
"""

import asyncio
from typing import Callable, Optional

from astrbot.api import logger


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
) -> None:
    """执行同步后的索引与衍生数据更新流程。"""
    docs_count = result.get("docs", 0) if result else 0

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
