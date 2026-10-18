"""
数据查看模块 —— 路由层

只读接口，供前端"数据查看"页面浏览各数据表的采集情况与数据内容。
所有接口均叠加服务层的查询护栏（表白名单、只读事务、语句超时、行数上限、
游标分页、并发闸门），避免慢查询拖累爬虫数据库。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dataview import services
from app.api.dataview.schemas import (
    DataRowsResponse,
    DataTableListResponse,
    DataTableOverviewResponse,
)
from app.api.users.models import User
from app.core.dependencies import require_viewer
from app.core.schemas.api_response import ApiResponse
from app.db.database import get_async_session

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/tables",
    response_model=ApiResponse[DataTableListResponse],
    summary="数据表清单与采集概况",
    description=(
        "列出爬虫库中所有数据表及其采集情况（记录数估算、占用空间、任务数、最近采集时间）。"
        "统计信息取自 PostgreSQL 系统视图并带有短 TTL 缓存，不会扫描业务数据。"
    ),
)
async def list_data_tables(
    operator: User = Depends(require_viewer),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[DataTableListResponse]:
    """列出所有数据表及采集概况。"""
    data = await services.list_data_tables(session)
    return ApiResponse.success(data=data)


@router.get(
    "/tables/{table_name}",
    response_model=ApiResponse[DataTableOverviewResponse],
    summary="单表采集概况",
    description=(
        "返回指定数据表的采集概况：估算记录数、占用空间、任务数、字段预览与最近任务。"
        "字段预览仅采样最新的少量记录（固定代价），不做全表扫描。"
    ),
)
async def get_table_overview(
    table_name: str = Path(..., description="数据表名（必须是爬虫库中已存在的数据表）"),
    operator: User = Depends(require_viewer),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[DataTableOverviewResponse]:
    """获取单个数据表的采集概况。"""
    data = await services.get_table_overview(table_name, session)
    return ApiResponse.success(data=data)


@router.get(
    "/tables/{table_name}/rows",
    response_model=ApiResponse[DataRowsResponse],
    summary="分页浏览数据表内容",
    description=(
        "按游标分页读取数据表内容。限制说明：单次最多返回 100 行；仅支持按 `_id` 排序；"
        "仅支持按任务 ID 精确过滤（走索引）；不接受 OFFSET 跳页；"
        "数据库侧语句超时 3 秒，超时会被熔断并返回 504。"
    ),
)
async def list_table_rows(
    table_name: str = Path(..., description="数据表名（必须是爬虫库中已存在的数据表）"),
    limit: Optional[int] = Query(None, ge=1, le=services.MAX_LIMIT, description="返回行数（1-100，默认 20）"),
    cursor: Optional[int] = Query(None, ge=1, description="游标：上一页最后一行的 _id"),
    task_id: Optional[str] = Query(None, max_length=64, description="按任务 ID 精确过滤"),
    order: Optional[str] = Query("desc", pattern="^(asc|desc)$", description="排序方向：desc=最新优先，asc=最早优先"),
    operator: User = Depends(require_viewer),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[DataRowsResponse]:
    """分页浏览指定数据表的数据内容。"""
    data = await services.fetch_table_rows(
        table_name,
        limit=limit,
        cursor=cursor,
        task_id=task_id,
        order=order,
        session=session,
    )
    return ApiResponse.success(data=data)
