"""
系统操作日志路由 — 全异步，纯审计日志（不含任务实时日志 WebSocket）
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.logs import schemas
from app.api.logs.services import LogsService
from app.db.database import get_async_session

router = APIRouter()


@router.post("/", response_model=schemas.SystemLogOut, summary="创建系统日志")
async def create_log(
    log_in: schemas.SystemLogCreate,
    session: AsyncSession = Depends(get_async_session),
) -> schemas.SystemLogOut:
    return await LogsService.add_log(session=session, log_data=log_in)


@router.get("/", response_model=List[schemas.SystemLogOut], summary="查询系统日志")
async def read_logs(
    skip: int = Query(0),
    limit: int = Query(100),
    level: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_async_session),
) -> List[schemas.SystemLogOut]:
    return await LogsService.get_log_list(session=session, skip=skip, limit=limit, level=level)


@router.get("/{log_id}", response_model=schemas.SystemLogOut, summary="获取指定日志详情")
async def read_log(
    log_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
) -> schemas.SystemLogOut:
    return await LogsService.get_log_detail(session=session, log_id=log_id)
