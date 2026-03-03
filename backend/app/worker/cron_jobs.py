import json
import logging
import uuid
from typing import Optional, List

from config import settings
from app.common.redis import redis_manager

logger = logging.getLogger(__name__)


async def dispatch_scheduled_task(
    spider_id: int,
    target_node_ids: Optional[List[str]] = None,
    timeout_seconds: int = 3600,
    **_extra: object,
) -> None:
    """
    供 APScheduler 触发调用的任务分发逻辑。
    通过 spider_id 查询 Spider 信息（language、source_url 等），构建 payload 后入列。
    """
    from app.db.database import async_session_maker
    from app.api.spiders.models import Spider
    from sqlalchemy import select

    task_id = f"cron-{uuid.uuid4().hex[:8]}"

    try:
        async with async_session_maker() as session:
            spider_result = await session.execute(
                select(Spider).where(Spider.id == spider_id)
            )
            spider = spider_result.scalars().first()
            if not spider:
                logger.error(f"Cron dispatch failed: Spider {spider_id} not found")
                return

        # 构建与 /api/tasks/run 一致的 task_payload
        task_payload = {
            "task_id": task_id,
            "spider_id": spider_id,
            "language": spider.language,
            "source_type": spider.source_type,
            "source_url": spider.source_url,
            "script_path": spider.command or "main.py",
            "timeout_seconds": timeout_seconds,
        }
        task_data = json.dumps(task_payload)

        if not redis_manager.client:
            logger.error("Redis client not available for cron dispatch")
            return

        queues: list[str] = []
        if target_node_ids:
            for node_id in target_node_ids:
                queues.append(f"{settings.NODE_QUEUE_PREFIX}{node_id}")
        else:
            queues.append(settings.PUBLIC_QUEUE_KEY)

        for target_queue in queues:
            await redis_manager.client.lpush(target_queue, task_data)

            node_identifier = target_queue.split(":")[-1] if target_queue != settings.PUBLIC_QUEUE_KEY else "public"
            status_key = f"task:status:{task_id}:{node_identifier}"
            initial_status = {
                "task_id": task_id,
                "spider_id": spider_id,
                "node_target": node_identifier,
                "status": "pending(cron)",
                "script_path": spider.command or "main.py",
            }
            await redis_manager.client.set(status_key, json.dumps(initial_status), ex=settings.TASK_STATUS_EXPIRE_SECONDS)

            logger.info(f"Cron Task {task_id} dispatched to {target_queue} for spider {spider_id}")

    except Exception as e:
        logger.error(f"Failed to dispatch scheduled task for spider {spider_id}: {e}")
