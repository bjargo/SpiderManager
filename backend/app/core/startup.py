"""
应用启动时的初始化任务模块

从 main.py 的 lifespan 中提取出来的启动清理逻辑，
保持 main.py 简洁，职责单一。
"""
import logging

from sqlalchemy import update

from app.api.tasks.models import SpiderTask
from app.core.timezone import now
from app.db.database import async_session_maker

logger = logging.getLogger(__name__)


async def clean_orphaned_tasks() -> None:
    """
    清理孤儿任务：将上次未正常结束的 running/pending 任务统一标记为 error。

    在服务启动时调用，防止历史残留的 running/pending 任务误导前端展示。
    所有受影响的任务会被标记为 error 状态并记录错误原因。

    :return: None
    """
    try:
        async with async_session_maker() as session:
            result = await session.execute(
                update(SpiderTask)
                .where(SpiderTask.status.in_(["running", "pending"]))
                .values(
                    status="error",
                    error_detail="Orphaned: server restarted before task completed",
                    finished_at=now(),
                )
            )
            await session.commit()
            if result.rowcount > 0:
                logger.warning(
                    f"Cleaned up {result.rowcount} orphaned task(s) on startup"
                )
    except Exception as e:
        logger.error(f"Failed to clean orphaned tasks: {e}")
