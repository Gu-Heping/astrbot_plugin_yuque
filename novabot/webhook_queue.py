"""
NovaBot Webhook 队列处理器
异步处理 Webhook 事件，减少更新延迟
"""

import asyncio
from collections import deque
from typing import Any, Callable, Optional

from astrbot.api import logger


class WebhookQueue:
    """Webhook 事件队列

    将 Webhook 事件加入队列，异步处理，避免阻塞响应。
    """

    def __init__(self, max_size: int = 100):
        """初始化队列

        Args:
            max_size: 队列最大大小
        """
        self._queue: deque = deque(maxlen=max_size)
        self._processor_task: Optional[asyncio.Task] = None
        self._running = False
        self._processor: Optional[Callable] = None
        self._stats = {
            "enqueued": 0,
            "processed": 0,
            "failed": 0,
            "dropped": 0,
        }

    def set_processor(self, processor: Callable):
        """设置事件处理器

        Args:
            processor: 异步处理函数 async def processor(event: dict)
        """
        self._processor = processor

    async def start(self):
        """启动队列处理器"""
        if self._running:
            return

        self._running = True
        self._processor_task = asyncio.create_task(self._process_loop())
        logger.info("[WebhookQueue] 队列处理器已启动")

    async def stop(self):
        """停止队列处理器"""
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
            self._processor_task = None
        logger.info("[WebhookQueue] 队列处理器已停止")

    async def enqueue(self, event: dict) -> bool:
        """将事件加入队列

        Args:
            event: Webhook 事件数据

        Returns:
            是否成功加入队列
        """
        if not self._running:
            logger.warning("[WebhookQueue] 队列未启动，事件被丢弃")
            self._stats["dropped"] += 1
            return False

        try:
            self._queue.append(event)
            self._stats["enqueued"] += 1
            logger.debug(
                f"[WebhookQueue] 事件入队: {event.get('action', 'unknown')}, "
                f"队列长度: {len(self._queue)}"
            )
            return True
        except Exception as e:
            logger.error(f"[WebhookQueue] 入队失败: {e}")
            self._stats["dropped"] += 1
            return False

    async def _process_loop(self):
        """持续处理队列"""
        while self._running:
            try:
                if not self._queue:
                    await asyncio.sleep(0.1)
                    continue

                event = self._queue.popleft()

                if self._processor:
                    try:
                        await self._processor(event)
                        self._stats["processed"] += 1
                    except Exception as e:
                        logger.error(f"[WebhookQueue] 处理事件失败: {e}", exc_info=True)
                        self._stats["failed"] += 1
                else:
                    logger.warning("[WebhookQueue] 未设置处理器")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[WebhookQueue] 处理循环出错: {e}", exc_info=True)
                await asyncio.sleep(1)

    def get_stats(self) -> dict:
        """获取队列统计"""
        return {
            "queue_length": len(self._queue),
            "is_running": self._running,
            **self._stats,
        }

    def clear(self):
        """清空队列"""
        self._queue.clear()
        logger.info("[WebhookQueue] 队列已清空")


# 全局队列实例（单例）
_webhook_queue: Optional[WebhookQueue] = None


def get_webhook_queue() -> WebhookQueue:
    """获取全局 Webhook 队列实例"""
    global _webhook_queue
    if _webhook_queue is None:
        _webhook_queue = WebhookQueue()
    return _webhook_queue