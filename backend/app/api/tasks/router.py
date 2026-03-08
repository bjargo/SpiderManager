import asyncio
import json
import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Query, Request, BackgroundTasks
from redis.asyncio import Redis
from sqlalchemy import select, func, desc
from app.db.database import get_async_session, async_session_maker
from sqlalchemy.ext.asyncio import AsyncSession

from redis.exceptions import RedisError
from apscheduler.triggers.cron import CronTrigger

from app.core.schemas.api_response import ApiResponse
from app.core.redis import get_redis, redis_manager
from config import settings
from app.api.tasks.schemas import TaskRequest, TaskListResponse, DataIngestRequest
from app.api.tasks.models import SpiderTask, TaskLog
from app.api.tasks.cron_schemas import CronTaskCreate, CronTaskResponse, CronTaskUpdate, CronTaskToggle
from app.api.spiders.models import Spider
from app.api.users.models import User
from app.core.dependencies import require_viewer, require_developer, verify_resource_owner
from app.core.enums import UserRole
from app.core.audit.service import audit_log
from app.core.timezone import now
from sqlalchemy import update as sa_update
from app.core.scheduler import scheduler
from app.worker.cron_jobs import dispatch_scheduled_task

router = APIRouter()
logger = logging.getLogger(__name__)

async def websocket_task_logs(websocket: WebSocket, task_id: str) -> None:
    """通过 Redis Pub/Sub 向前端推送任务实时日志"""
    await websocket.accept()

    if not redis_manager.client:
        await websocket.close(code=1011, reason="Redis inactive")
        return

    pubsub = redis_manager.client.pubsub()
    channel = f"log:channel:{task_id}"
    await pubsub.subscribe(channel)
    logger.info("WebSocket client connected for task %s", task_id)

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.1
            )
            if message and message["type"] == "message":
                data = message["data"]
                text = data.decode("utf-8") if isinstance(data, bytes) else data
                await websocket.send_text(text)
            else:
                await asyncio.sleep(0.01)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected for task %s", task_id)
    except Exception as e:
        logger.error("WebSocket error for task %s: %s", task_id, e)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()


async def websocket_task_data(websocket: WebSocket, task_id: str) -> None:
    """通过 Redis Pub/Sub 向前端实时推送任务采集数据"""
    await websocket.accept()

    if not redis_manager.client:
        await websocket.close(code=1011, reason="Redis inactive")
        return

    pubsub = redis_manager.client.pubsub()
    channel = f"data:channel:{task_id}"
    await pubsub.subscribe(channel)
    logger.info("WebSocket data client connected for task %s", task_id)

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.1
            )
            if message and message["type"] == "message":
                data = message["data"]
                text = data.decode("utf-8") if isinstance(data, bytes) else data
                await websocket.send_text(text)
            else:
                await asyncio.sleep(0.01)
    except WebSocketDisconnect:
        logger.info("WebSocket data client disconnected for task %s", task_id)
    except Exception as e:
        logger.error("WebSocket data error for task %s: %s", task_id, e)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()

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
    """
    try:
        query = select(SpiderTask).where(SpiderTask.is_deleted == False)
        count_query = select(func.count()).select_from(SpiderTask).where(SpiderTask.is_deleted == False)

        if status_filter:
            query = query.where(SpiderTask.status == status_filter)
            count_query = count_query.where(SpiderTask.status == status_filter)
        if spider_id:
            query = query.where(SpiderTask.spider_id == spider_id)
            count_query = count_query.where(SpiderTask.spider_id == spider_id)
        if task_id:
            query = query.where(SpiderTask.task_id == task_id)
            count_query = count_query.where(SpiderTask.task_id == task_id)

        # ── Time filtering ──
        # asyncpg 对参数类型推断严格，必须传 datetime 对象而非字符串，
        # 否则会绑定为 VARCHAR 导致与 TIMESTAMP 列比较时类型错误。
        _TIME_FMT = "%Y-%m-%d %H:%M:%S"
        try:
            if start_time:
                dt_start = datetime.strptime(start_time, _TIME_FMT)
                query = query.where(SpiderTask.created_at >= dt_start)
                count_query = count_query.where(SpiderTask.created_at >= dt_start)
            if end_time:
                dt_end = datetime.strptime(end_time, _TIME_FMT)
                query = query.where(SpiderTask.created_at <= dt_end)
                count_query = count_query.where(SpiderTask.created_at <= dt_end)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="时间格式错误，请使用 YYYY-MM-DD HH:MM:SS 格式",
            )

        # Count total
        count_result = await session.execute(count_query)
        total = count_result.scalar_one()

        # Fetch page. Use id.desc() instead of created_at.desc() to leverage PK index for huge performance gain
        query = query.order_by(desc(SpiderTask.id)).offset(skip).limit(limit)
        result = await session.execute(query)
        items = result.scalars().all()

        return ApiResponse.success(data=TaskListResponse(items=items, total=total))
    except Exception as e:
        logger.error(f"Failed to fetch task list: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch task list: {str(e)}"
        )

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
    强制终止正在运行的任务。
    1. 先在数据库中将状态直接置为 cancelled，确保前端能立即看到状态变化。
    2. 同时通过 Redis 下发 kill signal，通知 Worker 杀掉实际子进程。
    """
    # ── 1. 前置校验：只有 running / pending 状态才允许终止 ──
    result = await session.execute(
        select(SpiderTask).where(SpiderTask.task_id == task_id)
    )
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.is_deleted:
        raise HTTPException(status_code=404, detail="Task not found")

    spider = await session.get(Spider, task.spider_id)
    if spider:
        verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    if task.status not in ("running", "pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task is already in '{task.status}' state, cannot stop",
        )

    # ── 2. 直接更新数据库状态为 cancelled ──
    try:
        await session.execute(
            sa_update(SpiderTask)
            .where(SpiderTask.task_id == task_id)
            .values(
                status="cancelled",
                finished_at=now(),
                error_detail="Cancelled by user",
            )
        )
        await session.commit()
    except Exception as e:
        logger.error(f"Failed to update task {task_id} status in DB: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update task status: {str(e)}",
        )

    # ── 3. 通过 Redis 通知 Worker 杀进程 ──
    try:
        kill_key = f"task:kill:{task_id}"
        await redis.set(kill_key, "1", ex=120)

        channel = f"log:channel:{task_id}"
        await redis.publish(channel, "[SYSTEM: Kill signal sent to worker...]")

        logger.info(f"Kill signal sent for task {task_id}")
    except RedisError as e:
        # Redis 异常不应阻断整个终止流程，DB 已更新成功
        logger.warning(f"Redis error when sending kill signal for task {task_id}: {e}")

    # ── 4. 记录审计日志：停止任务 ──
    # 使用 @audit_log 装饰器自动捕获

    return ApiResponse.success(data={"task_id": task_id}, message="Task cancelled")


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
    软删除指定任务记录（标记 is_deleted = True）。
    仅允许删除非 running 状态的任务。
    同时清理该任务在 Redis 中可能残留的未处理数据缓存。
    """

    # 校验任务存在性和状态
    result = await session.execute(
        select(SpiderTask).where(SpiderTask.task_id == task_id)
    )
    task = result.scalars().first()

    if task:
        spider = await session.get(Spider, task.spider_id)
        if spider:
            verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    if task.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    if task.status == "running":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a running task. Please stop it first.",
        )

    try:
        # 软删除：标记 is_deleted = True，不再删除关联日志
        await session.execute(
            sa_update(SpiderTask)
            .where(SpiderTask.task_id == task_id)
            .values(is_deleted=True)
        )
        await session.commit()
        logger.info(f"Task {task_id} soft-deleted successfully")
    except Exception as e:
        logger.error(f"Failed to delete task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete task: {str(e)}",
        )

    # ── 清理 Redis 中该任务的残留数据缓存 ──
    try:
        # 1. 清理 task:status:{task_id}:* 系列键
        async for key in redis.scan_iter(match=f"task:status:{task_id}:*", count=100):
            await redis.delete(key)

        # 2. 清理 kill signal 键
        await redis.delete(f"task:kill:{task_id}")

        logger.info(f"Redis residual keys cleaned for task {task_id}")
    except RedisError as e:
        # Redis 清理失败不应阻断删除流程
        logger.warning(f"Failed to clean Redis residual data for task {task_id}: {e}")

    return ApiResponse.success(data={"task_id": task_id}, message="Task deleted")


@router.get("/{task_id}/logs", response_model=ApiResponse, summary="查询任务历史日志")
async def get_task_logs(
    task_id: str,
    skip: int = Query(0, description="跳过记录数"),
    limit: int = Query(2000, description="返回记录数"),
    session: AsyncSession = Depends(get_async_session),
):
    """
    从数据库中查询指定任务的历史日志记录（按时间升序）。
    用于非 running 状态的任务日志回放。
    """

    try:
        result = await session.execute(
            select(TaskLog)
            .where(TaskLog.task_id == task_id)
            .order_by(TaskLog.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        logs = result.scalars().all()

        log_items = [
            {"id": log.id, "content": log.content, "created_at": str(log.created_at)}
            for log in logs
        ]

        # 如果没有日志记录，但任务有 error_detail，则补充一条系统日志
        if not log_items and skip == 0:
            task_result = await session.execute(
                select(SpiderTask).where(SpiderTask.task_id == task_id)
            )
            task_obj = task_result.scalars().first()
            if task_obj and task_obj.error_detail:
                log_items.append({
                    "id": 0,
                    "content": f"[SYSTEM ERROR]: {task_obj.error_detail}",
                    "created_at": str(task_obj.finished_at or task_obj.updated_at or now())
                })

        return ApiResponse.success(data=log_items)
    except Exception as e:
        logger.error(f"Failed to fetch logs for task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch task logs: {str(e)}",
        )


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
    高并发数据接入网关。

    爬虫在运行过程中采集到数据后，调用此接口将数据快速压入 Redis 队列，
    由下游消费者异步批量落库到 Postgres，实现采集与存储的完全解耦。

    **设计约束**：
    - 接口内部严禁任何 Postgres 同步操作
    - 响应时间需维持在毫秒级，以支撑百级别爬虫并发
    """
    timestamp = now().isoformat()

    try:
        message = json.dumps(
            {
                "t": body.table_name,
                "d": body.data,
                "task_id": task_id,
                "ts": timestamp,
            },
            ensure_ascii=False,
        )
        await redis.lpush(settings.INGEST_QUEUE_KEY, message)
    except RedisError as e:
        logger.error("Redis error during data ingestion for task %s: %s", task_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"数据接入失败，Redis 服务异常: {str(e)}",
        )
    except (TypeError, ValueError) as e:
        logger.error("Serialization error during data ingestion for task %s: %s", task_id, e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"数据序列化失败: {str(e)}",
        )

    return ApiResponse.success(
        data={"task_id": task_id, "count": len(body.data)},
        message="数据已接入队列",
    )


@router.post("/run", response_model=ApiResponse, summary="下发爬虫任务")
async def run_task(request: TaskRequest, redis: Redis = Depends(get_redis)):
    """
    根据传入的 spider_id 查询爬虫配置（language、source_url 等），
    构建完整 payload 并下发到指定的 node 队列或公共队列。
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(Spider).where(Spider.id == request.spider_id, Spider.is_deleted == False)
        )
        spider = result.scalars().first()

    if not spider or spider.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Spider {request.spider_id} not found."
        )

    try:
        task_payload = {
            "task_id": request.task_id,
            "spider_id": request.spider_id,
            "project_id": spider.project_id,
            "language": spider.language or "default",
            "source_type": spider.source_type,
            "source_url": spider.source_url,
            "script_path": request.script_path,
            "timeout_seconds": request.timeout_seconds,
        }
        task_data = json.dumps(task_payload)

        # ── 创建持久化任务记录 ──
        async with async_session_maker() as session:
            db_task = SpiderTask(
                task_id=request.task_id,
                spider_id=request.spider_id,
                spider_name=spider.name,
                status="pending",
                command=spider.command,
            )
            session.add(db_task)
            await session.commit()

        target_queues: list[str] = []
        if request.target_node_ids:
            for node_id in request.target_node_ids:
                target_queues.append(f"{settings.NODE_QUEUE_PREFIX}{node_id}")
        else:
            target_queues.append(settings.PUBLIC_QUEUE_KEY)

        for target_queue in target_queues:
            await redis.lpush(target_queue, task_data)

            node_identifier = target_queue.split(":")[-1] if target_queue != settings.PUBLIC_QUEUE_KEY else "public"
            status_key = f"task:status:{request.task_id}:{node_identifier}"

            initial_status = {
                "task_id": request.task_id,
                "spider_id": request.spider_id,
                "node_target": node_identifier,
                "status": "pending",
                "script_path": request.script_path,
            }
            await redis.set(status_key, json.dumps(initial_status), ex=settings.TASK_STATUS_EXPIRE_SECONDS)
            logger.info(f"Task {request.task_id} dispatched to {target_queue}")

        return ApiResponse.success(data={"task_id": request.task_id, "queues": target_queues}, message="Task dispatched successfully")

    except RedisError as e:
        logger.error(f"Redis error when dispatching task {request.task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to dispatch task due to Redis error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error dispatching task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred while dispatching task"
        )


# ─────────────────────────────────────────────────
# Cron 定时调度相关路由
# ─────────────────────────────────────────────────

def _generate_job_id() -> str:
    """生成唯一的 Job ID"""
    import uuid
    return f"schedule-{uuid.uuid4().hex[:8]}"


async def _get_spider_name(spider_id: int) -> str:
    """查询 Spider 名称，查不到返回空字符串"""
    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(Spider.name).where(Spider.id == spider_id)
            )
            row = result.scalar_one_or_none()
            return row or ""
    except Exception:
        return ""


def _build_cron_response(job: "Job") -> CronTaskResponse:
    """从 APScheduler Job 对象构建 CronTaskResponse（不含 spider_name）"""
    kwargs = job.kwargs or {}
    return CronTaskResponse(
        job_id=job.id,
        spider_id=kwargs.get("spider_id", 0),
        cron_expr=kwargs.get("cron_expr", str(job.trigger)),
        description=kwargs.get("description"),
        enabled=job.next_run_time is not None,
        target_node_ids=kwargs.get("target_node_ids"),
        next_run_time=str(job.next_run_time) if job.next_run_time else None,
        owner_id=kwargs.get("owner_id"),
    )


@router.post("/cron", response_model=CronTaskResponse, summary="添加定时爬虫任务")
@audit_log(action="CREATE", resource_type="cron_task")
async def add_cron_task(
    body: CronTaskCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    向 APScheduler 添加一个基于 Cron 表达式的定时任务。
    当达到触发时间时，调度器会自动把任务发布到 Redis 队列。
    """
    try:
        trigger = CronTrigger.from_crontab(body.cron_expr)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 Cron 表达式: {str(e)}"
        )

    job_id = _generate_job_id()

    try:
        job = scheduler.add_job(
            dispatch_scheduled_task,
            trigger=trigger,
            id=job_id,
            kwargs={
                "spider_id": body.spider_id,
                "target_node_ids": body.target_node_ids,
                "timeout_seconds": body.timeout_seconds,
                # 额外元数据存入 kwargs 以便后续读取
                "cron_expr": body.cron_expr,
                "description": body.description,
                "owner_id": operator.id,
            },
            replace_existing=True,
        )

        # 如果创建时 enabled=False，立即暂停
        if not body.enabled:
            job.pause()

        spider_name = await _get_spider_name(body.spider_id)

        logger.info(f"Cron task {job_id} added for spider {body.spider_id} with expr {body.cron_expr}")

        resp = _build_cron_response(job)
        resp.spider_name = spider_name
        # enabled 需要根据请求手动设置，因为 pause 后 next_run_time 为 None
        resp.enabled = body.enabled
        return resp

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add cron task: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"添加定时任务失败: {str(e)}"
        )


@router.get("/cron", response_model=List[CronTaskResponse], summary="查询所有定时记录")
async def get_cron_tasks():
    """
    获取 APScheduler 中所有定时任务列表（含暂停的）。
    """
    try:
        jobs = scheduler.get_jobs()
        response_list: list[CronTaskResponse] = []

        # 批量查询所有 spider_id 对应的名称
        spider_ids = set()
        for job in jobs:
            kwargs = job.kwargs or {}
            sid = kwargs.get("spider_id")
            if sid is not None:
                spider_ids.add(sid)

        spider_name_map: dict[int, str] = {}
        if spider_ids:
            try:
                async with async_session_maker() as session:
                    result = await session.execute(
                        select(Spider.id, Spider.name).where(Spider.id.in_(spider_ids))
                    )
                    for row in result:
                        spider_name_map[row[0]] = row[1]
            except Exception as e:
                logger.warning(f"Failed to fetch spider names: {e}")

        for job in jobs:
            kwargs = job.kwargs or {}
            # 只返回有关联 spider_id 的任务，过滤掉系统内置任务（如 daily_image_prune）
            if "spider_id" not in kwargs:
                continue

            resp = _build_cron_response(job)
            resp.spider_name = spider_name_map.get(resp.spider_id, "")
            response_list.append(resp)

        return response_list
    except Exception as e:
        logger.error(f"Failed to fetch cron tasks: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取定时任务列表失败"
        )


@router.post("/cron/{job_id}/delete", summary="删除指定的定时任务")
@audit_log(action="DELETE", resource_type="cron_task")
async def delete_cron_task(
    job_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    根据 Job ID 删除 APScheduler 中的任务。
    """
    try:
        job = scheduler.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"定时任务未找到或删除失败"
            )

        scheduler.remove_job(job_id)

        logger.info(f"Cron task {job_id} removed successfully")
        return {"message": "定时任务已删除", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove cron task {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"定时任务未找到或删除失败: {str(e)}"
        )


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
    """
    try:
        job = scheduler.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"定时任务 {job_id} 不存在"
            )

        if body.enabled:
            job.resume()
        else:
            job.pause()

        logger.info(f"Cron task {job_id} {'enabled' if body.enabled else 'disabled'}")

        spider_name = await _get_spider_name((job.kwargs or {}).get("spider_id", 0))
        resp = _build_cron_response(job)
        resp.spider_name = spider_name
        resp.enabled = body.enabled
        return resp

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle cron task {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"切换定时任务失败: {str(e)}"
        )


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
    """
    try:
        job = scheduler.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"定时任务 {job_id} 不存在"
            )

        current_kwargs = dict(job.kwargs or {})

        original_kwargs = dict(current_kwargs)

        # 部分覆盖 kwargs
        if body.spider_id is not None:
            current_kwargs["spider_id"] = body.spider_id
        if body.description is not None:
            current_kwargs["description"] = body.description
        if body.target_node_ids is not None:
            current_kwargs["target_node_ids"] = body.target_node_ids if body.target_node_ids else None
        if body.timeout_seconds is not None:
            current_kwargs["timeout_seconds"] = body.timeout_seconds

        # 如果修改了 cron 表达式
        if body.cron_expr is not None:
            try:
                trigger = CronTrigger.from_crontab(body.cron_expr)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"无效的 Cron 表达式: {str(e)}"
                )
            current_kwargs["cron_expr"] = body.cron_expr
            job.modify(kwargs=current_kwargs)
            job.reschedule(trigger=trigger)
        else:
            job.modify(kwargs=current_kwargs)

        # 处理 enabled 切换
        if body.enabled is not None:
            if body.enabled:
                job.resume()
            else:
                job.pause()

        logger.info(f"Cron task {job_id} updated successfully")

        spider_name = await _get_spider_name(current_kwargs.get("spider_id", 0))
        resp = _build_cron_response(job)
        resp.spider_name = spider_name
        if body.enabled is not None:
            resp.enabled = body.enabled
        return resp

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update cron task {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"更新定时任务失败: {str(e)}"
        )

