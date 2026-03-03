"""
系统操作日志 — 数据访问层 (全异步)
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.logs.models import SystemLog
from app.api.logs.schemas import SystemLogCreate


class LogsCRUD:
    """封装底层数据库操作，对外提供统一的异步接口"""

    @staticmethod
    async def create_log(session: AsyncSession, log_in: SystemLogCreate) -> SystemLog:
        """向数据库写入一条新的系统操作日志"""
        db_log = SystemLog(
            level=log_in.level,
            action=log_in.action,
            user_id=log_in.user_id,
            message=log_in.message,
        )
        session.add(db_log)
        await session.commit()
        await session.refresh(db_log)
        return db_log

    @staticmethod
    async def get_logs(
        session: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        level: Optional[str] = None,
    ) -> List[SystemLog]:
        """分页查询日志，支持按级别过滤"""
        stmt = select(SystemLog)
        if level:
            stmt = stmt.where(SystemLog.level == level)
        stmt = stmt.order_by(SystemLog.created_at.desc()).offset(skip).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_log_by_id(session: AsyncSession, log_id: int) -> Optional[SystemLog]:
        """根据日志 ID 查询详细内容"""
        return await session.get(SystemLog, log_id)
