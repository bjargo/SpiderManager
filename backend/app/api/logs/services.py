"""
系统操作日志 — 业务逻辑层 (全异步)
"""
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.logs.crud import LogsCRUD
from app.api.logs.models import SystemLog
from app.api.logs.schemas import SystemLogCreate

# 允许的日志级别白名单
_VALID_LEVELS = frozenset({"INFO", "WARNING", "ERROR"})


class LogsService:
    """系统操作日志核心业务逻辑，负责调度 CRUD 层和处理业务规则"""

    @staticmethod
    async def add_log(session: AsyncSession, log_data: SystemLogCreate) -> SystemLog:
        """添加新的系统日志，对非法级别做降级处理"""
        if log_data.level not in _VALID_LEVELS:
            log_data.level = "INFO"
        return await LogsCRUD.create_log(session=session, log_in=log_data)

    @staticmethod
    async def get_log_list(
        session: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        level: Optional[str] = None,
    ) -> List[SystemLog]:
        """获取日志列表"""
        return await LogsCRUD.get_logs(session=session, skip=skip, limit=limit, level=level)

    @staticmethod
    async def get_log_detail(session: AsyncSession, log_id: int) -> SystemLog:
        """获取特定日志的详细信息，不存在则抛出 404"""
        log = await LogsCRUD.get_log_by_id(session=session, log_id=log_id)
        if not log:
            raise HTTPException(status_code=404, detail="Log not found")
        return log
