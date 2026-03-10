"""
任务管理路由 — 纯接口层

所有业务逻辑已拆分到以下模块：
- services.py    — 任务 CRUD、查询、数据接入、任务派发
- cron_services.py — Cron 定时调度逻辑
- websocket.py    — WebSocket 实时日志 / 数据推送

本模块仅负责路由注册、请求参数解析和统一响应格式。
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas.api_response import ApiResponse
from app.core.redis import get_redis
from app.db.database import get_async_session
from app.api.tasks.schemas import TaskRequest, TaskListResponse, DataIngestRequest
from app.api.tasks.cron_schemas import CronTaskCreate, CronTaskResponse, CronTaskUpdate, CronTaskToggle
from app.api.users.models import User
from app.core.dependencies import require_viewer, require_developer
from app.core.audit.service import audit_log
from app.api.tasks import services
from app.api.tasks import cron_services

# WebSocket handlers 从 websocket 模块导入，供 main.py 注册
from app.api.tasks.websocket import websocket_task_logs, websocket_task_data  # noqa: F401

router = APIRouter()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────
# 任务 CRUD 路由
# ─────────────────────────────────────────────────


@router.get("", response_model=ApiResponse[TaskListResponse], summary="查询所有最近任务(分页+筛选)")
async def get_all_tasks(
    status_filter: str | None = Query(None, alias="status"),
    spider_id: int | None = Query(None),
    task_id: str | None = Query(None),
    start_time: str | None = Query(None, description="任务创建时间 >= 该值, YYYY-MM-DD HH:MM:SS"),
    end_time: str | None = Query(None, description="任务创建时间 <= 该值, YYYY-MM-DD HH:MM:SS"),
    skip: int = Query(0),
    limit: int = Query(50),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    """
    带分页和条件过滤的任务列表查询。

    :param status_filter: 状态筛选
    :param spider_id: 按爬虫 ID 筛选
    :param task_id: 按任务 ID 精确匹配
    :param start_time: 创建时间起始
    :param end_time: 创建时间截止
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含任务列表和总数
    """
    data = await services.get_all_tasks(
        status_filter, spider_id, task_id,
        start_time, end_time, skip, limit, session,
    )
    return ApiResponse.success(data=data)


@router.post("/{task_id}/stop", response_model=ApiResponse, summary="强制停止运行中的任务")
@audit_log(action="STOP", resource_type="task")
async def stop_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    强制终止正在运行的任务（更新数据库 + 发送 Redis kill 信号）。

    :param task_id: 任务 UUID
    :param background_tasks: 后台任务（审计日志使用）
    :param redis: 注入的 Redis 客户端
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含 task_id
    """
    data = await services.stop_task(task_id, operator, redis, session)
    return ApiResponse.success(data=data, message="Task cancelled")


@router.post("/{task_id}/delete", response_model=ApiResponse, summary="删除任务记录")
@audit_log(action="DELETE", resource_type="task")
async def delete_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    软删除指定任务记录并清理 Redis 残留数据。

    :param task_id: 任务 UUID
    :param background_tasks: 后台任务（审计日志使用）
    :param redis: 注入的 Redis 客户端
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含 task_id
    """
    data = await services.delete_task(task_id, operator, redis, session)
    return ApiResponse.success(data=data, message="Task deleted")


@router.get("/{task_id}/logs", response_model=ApiResponse, summary="查询任务历史日志")
async def get_task_logs(
    task_id: str,
    skip: int = Query(0, description="跳过记录数"),
    limit: int = Query(2000, description="返回记录数"),
    session: AsyncSession = Depends(get_async_session),
):
    """
    从数据库中查询指定任务的历史日志记录。

    :param task_id: 任务 UUID
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 注入的数据库会话
    :return: ApiResponse 包含日志列表
    """
    data = await services.get_task_logs(task_id, skip, limit, session)
    return ApiResponse.success(data=data)


# ─────────────────────────────────────────────────
# 任务采集数据查询路由
# ─────────────────────────────────────────────────


@router.get("/{task_id}/data", response_model=ApiResponse, summary="查询任务采集数据")
async def get_task_data(
    task_id: str,
    table_name: str | None = Query(None, description="数据表名，不传则自动查询任务关联的表"),
    skip: int = Query(0, description="跳过记录数"),
    limit: int = Query(100, description="返回记录数，最大500"),
    session: AsyncSession = Depends(get_async_session),
):
    """
    查询指定任务采集的数据记录，支持分页查询。

    :param task_id: 任务 UUID
    :param table_name: 数据表名
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 注入的数据库会话
    :return: ApiResponse 包含数据列表和总数
    """
    data = await services.get_task_data(task_id, table_name, skip, limit, session)
    return ApiResponse.success(data=data)


# ─────────────────────────────────────────────────
# 数据 Ingestion 路由 (高并发接入层)
# ─────────────────────────────────────────────────


@router.post("/data/ingest", response_model=ApiResponse, summary="爬虫数据接入（高并发Gateway）")
async def ingest_data(
    task_id: str = Query(..., min_length=1, description="关联的任务 ID"),
    body: DataIngestRequest = ...,
    redis: Redis = Depends(get_redis),
) -> ApiResponse:
    """
    高并发数据接入网关，将爬虫采集数据压入 Redis 队列。

    :param task_id: 关联的任务 UUID
    :param body: 数据接入请求体
    :param redis: 注入的 Redis 客户端
    :return: ApiResponse 包含 task_id 和数据条数
    """
    data = await services.ingest_data(task_id, body, redis)
    return ApiResponse.success(data=data, message="数据已接入队列")


@router.post("/run", response_model=ApiResponse, summary="下发爬虫任务")
async def run_task(request: TaskRequest, redis: Redis = Depends(get_redis)):
    """
    根据 spider_id 构建 payload 并下发到 Redis 队列。

    :param request: 任务下发请求体
    :param redis: 注入的 Redis 客户端
    :return: ApiResponse 包含 task_id 和目标队列
    """
    data = await services.run_task(request, redis)
    return ApiResponse.success(data=data, message="Task dispatched successfully")


# ─────────────────────────────────────────────────
# Cron 定时调度路由
# ─────────────────────────────────────────────────


@router.post("/cron", response_model=CronTaskResponse, summary="添加定时爬虫任务")
@audit_log(action="CREATE", resource_type="cron_task")
async def add_cron_task(
    body: CronTaskCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    向 APScheduler 添加基于 Cron 表达式的定时任务。

    :param body: 定时任务创建请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: CronTaskResponse 创建结果
    """
    return await cron_services.add_cron_task(body, operator)


@router.get("/cron", response_model=List[CronTaskResponse], summary="查询所有定时记录")
async def get_cron_tasks():
    """
    获取 APScheduler 中所有的定时任务列表。

    :return: CronTaskResponse 列表
    """
    return await cron_services.get_cron_tasks()


@router.post("/cron/{job_id}/delete", summary="删除指定的定时任务")
@audit_log(action="DELETE", resource_type="cron_task")
async def delete_cron_task(
    job_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    根据 Job ID 删除 APScheduler 中的定时任务。

    :param job_id: APScheduler 任务 ID
    :param background_tasks: 后台任务（审计日志使用）
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: 删除结果字典
    """
    return await cron_services.delete_cron_task(job_id)


@router.post("/cron/{job_id}/toggle", response_model=CronTaskResponse, summary="切换定时任务开关")
@audit_log(action="TOGGLE", resource_type="cron_task")
async def toggle_cron_task(
    job_id: str,
    body: CronTaskToggle,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    暂停或恢复指定的定时任务。

    :param job_id: APScheduler 任务 ID
    :param body: 包含 enabled 字段的请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: CronTaskResponse 更新后的任务状态
    """
    return await cron_services.toggle_cron_task(job_id, body)


@router.post("/cron/{job_id}/update", response_model=CronTaskResponse, summary="修改指定的定时任务")
@audit_log(action="UPDATE", resource_type="cron_task")
async def update_cron_task(
    job_id: str,
    body: CronTaskUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    修改已有定时任务的属性（爬虫关联、cron 表达式、描述、节点等）。

    :param job_id: APScheduler 任务 ID
    :param body: 定时任务更新请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: CronTaskResponse 更新后的任务状态
    """
    return await cron_services.update_cron_task(job_id, body)
