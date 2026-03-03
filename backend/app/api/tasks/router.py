import asyncio
import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Query
from redis.asyncio import Redis
from sqlalchemy import select, func, desc
from app.db.database import get_async_session, async_session_maker
from sqlalchemy.ext.asyncio import AsyncSession

from redis.exceptions import RedisError
from apscheduler.triggers.cron import CronTrigger

from app.common.schemas.api_response import ApiResponse
from app.common.redis import get_redis, redis_manager
from config import settings
from app.api.tasks.schemas import TaskRequest, TaskListResponse
from app.api.tasks.models import SpiderTask
from app.api.tasks.cron_schemas import CronTaskCreate, CronTaskResponse, CronTaskUpdate, CronTaskToggle
from app.api.spiders.models import Spider
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

@router.get("/", response_model=ApiResponse[TaskListResponse], summary="获取任务列表")
async def get_task_list(
    skip: int = Query(0, description="跳过记录数"),
    limit: int = Query(50, description="返回记录数"),
    status_filter: str | None = Query(None, alias="status", description="任务状态过滤"),
    spider_id: int | None = Query(None, description="所属爬虫ID过滤"),
    session: AsyncSession = Depends(get_async_session),
):
    """
    带分页和条件过滤的任务列表查询。
    """
    try:
        query = select(SpiderTask)
        count_query = select(func.count()).select_from(SpiderTask)

        if status_filter:
            query = query.where(SpiderTask.status == status_filter)
            count_query = count_query.where(SpiderTask.status == status_filter)
        if spider_id:
            query = query.where(SpiderTask.spider_id == spider_id)
            count_query = count_query.where(SpiderTask.spider_id == spider_id)

        # Count total
        count_result = await session.execute(count_query)
        total = count_result.scalar_one()

        # Fetch page
        query = query.order_by(desc(SpiderTask.created_at)).offset(skip).limit(limit)
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
async def stop_task(
    task_id: str,
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_async_session),
):
    """
    强制终止正在运行的任务。
    1. 先在数据库中将状态直接置为 cancelled，确保前端能立即看到状态变化。
    2. 同时通过 Redis 下发 kill signal，通知 Worker 杀掉实际子进程。
    """
    from sqlalchemy import update as sa_update

    # ── 1. 前置校验：只有 running / pending 状态才允许终止 ──
    result = await session.execute(
        select(SpiderTask).where(SpiderTask.task_id == task_id)
    )
    task = result.scalars().first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    if task.status not in ("running", "pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task is already in '{task.status}' state, cannot stop",
        )

    # ── 2. 直接更新数据库状态为 cancelled ──
    try:
        from datetime import datetime
        await session.execute(
            sa_update(SpiderTask)
            .where(SpiderTask.task_id == task_id)
            .values(
                status="cancelled",
                finished_at=datetime.utcnow(),
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

    return ApiResponse.success(data={"task_id": task_id}, message="Task cancelled")


@router.post("/{task_id}/delete", response_model=ApiResponse, summary="删除任务记录")
async def delete_task(
    task_id: str,
    session: AsyncSession = Depends(get_async_session),
):
    """
    删除指定任务的数据库记录，同时级联删除关联的日志。
    仅允许删除非 running 状态的任务。
    """
    from sqlalchemy import delete as sa_delete
    from app.api.tasks.task_log_models import TaskLog

    # 校验任务存在性和状态
    result = await session.execute(
        select(SpiderTask).where(SpiderTask.task_id == task_id)
    )
    task = result.scalars().first()

    if not task:
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
        # 先删关联日志，再删任务本身
        await session.execute(
            sa_delete(TaskLog).where(TaskLog.task_id == task_id)
        )
        await session.execute(
            sa_delete(SpiderTask).where(SpiderTask.task_id == task_id)
        )
        await session.commit()
        logger.info(f"Task {task_id} and its logs deleted successfully")
        return ApiResponse.success(data={"task_id": task_id}, message="Task deleted")
    except Exception as e:
        logger.error(f"Failed to delete task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete task: {str(e)}",
        )


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
    from app.api.tasks.task_log_models import TaskLog

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
        return ApiResponse.success(data=log_items)
    except Exception as e:
        logger.error(f"Failed to fetch logs for task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch task logs: {str(e)}",
        )


@router.post("/run", response_model=ApiResponse, summary="下发爬虫任务")
async def run_task(request: TaskRequest, redis: Redis = Depends(get_redis)):
    """
    根据传入的 spider_id 查询爬虫配置（language、source_url 等），
    构建完整 payload 并下发到指定的 node 队列或公共队列。
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(Spider).where(Spider.id == request.spider_id)
        )
        spider = result.scalars().first()

    if not spider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Spider {request.spider_id} not found."
        )

    try:
        task_payload = {
            "task_id": request.task_id,
            "spider_id": request.spider_id,
            "language": spider.language,
            "source_type": spider.source_type,
            "source_url": spider.source_url,
            "script_path": request.script_path,
            "timeout_seconds": request.timeout_seconds,
        }
        task_data = json.dumps(task_payload)

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
    )


@router.post("/cron", response_model=CronTaskResponse, summary="添加定时爬虫任务")
async def add_cron_task(request: CronTaskCreate):
    """
    向 APScheduler 添加一个基于 Cron 表达式的定时任务。
    当达到触发时间时，调度器会自动把任务发布到 Redis 队列。
    """
    try:
        trigger = CronTrigger.from_crontab(request.cron_expr)
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
                "spider_id": request.spider_id,
                "target_node_ids": request.target_node_ids,
                "timeout_seconds": request.timeout_seconds,
                # 额外元数据存入 kwargs 以便后续读取
                "cron_expr": request.cron_expr,
                "description": request.description,
            },
            replace_existing=True,
            misfire_grace_time=60,
        )

        # 如果创建时 enabled=False，立即暂停
        if not request.enabled:
            job.pause()

        spider_name = await _get_spider_name(request.spider_id)

        logger.info(f"Cron task {job_id} added for spider {request.spider_id} with expr {request.cron_expr}")

        resp = _build_cron_response(job)
        resp.spider_name = spider_name
        # enabled 需要根据请求手动设置，因为 pause 后 next_run_time 为 None
        resp.enabled = request.enabled
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
async def delete_cron_task(job_id: str):
    """
    根据 Job ID 删除 APScheduler 中的任务。
    """
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Cron task {job_id} removed successfully")
        return {"message": "定时任务已删除", "job_id": job_id}
    except Exception as e:
        logger.error(f"Failed to remove cron task {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"定时任务未找到或删除失败: {str(e)}"
        )


@router.post("/cron/{job_id}/toggle", response_model=CronTaskResponse, summary="切换定时任务开关")
async def toggle_cron_task(job_id: str, request: CronTaskToggle):
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

        if request.enabled:
            job.resume()
        else:
            job.pause()

        logger.info(f"Cron task {job_id} {'enabled' if request.enabled else 'disabled'}")

        spider_name = await _get_spider_name((job.kwargs or {}).get("spider_id", 0))
        resp = _build_cron_response(job)
        resp.spider_name = spider_name
        resp.enabled = request.enabled
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
async def update_cron_task(job_id: str, request: CronTaskUpdate):
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

        # 部分覆盖 kwargs
        if request.spider_id is not None:
            current_kwargs["spider_id"] = request.spider_id
        if request.description is not None:
            current_kwargs["description"] = request.description
        if request.target_node_ids is not None:
            current_kwargs["target_node_ids"] = request.target_node_ids if request.target_node_ids else None
        if request.timeout_seconds is not None:
            current_kwargs["timeout_seconds"] = request.timeout_seconds

        # 如果修改了 cron 表达式
        if request.cron_expr is not None:
            try:
                trigger = CronTrigger.from_crontab(request.cron_expr)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"无效的 Cron 表达式: {str(e)}"
                )
            current_kwargs["cron_expr"] = request.cron_expr
            job.modify(kwargs=current_kwargs)
            job.reschedule(trigger=trigger)
        else:
            job.modify(kwargs=current_kwargs)

        # 处理 enabled 切换
        if request.enabled is not None:
            if request.enabled:
                job.resume()
            else:
                job.pause()

        logger.info(f"Cron task {job_id} updated successfully")

        spider_name = await _get_spider_name(current_kwargs.get("spider_id", 0))
        resp = _build_cron_response(job)
        resp.spider_name = spider_name
        if request.enabled is not None:
            resp.enabled = request.enabled
        return resp

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update cron task {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"更新定时任务失败: {str(e)}"
        )

