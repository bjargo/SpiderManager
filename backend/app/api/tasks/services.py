"""
任务管理业务逻辑层

将 router.py 中的任务 CRUD、数据查询和任务派发逻辑全部迁移至此，
router 仅负责接口响应和参数解析。
"""
import json
import logging
from datetime import datetime

from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, func, desc, text
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.spiders.models import Spider
from app.api.tasks.models import SpiderTask, TaskLog
from app.api.tasks.schemas import TaskRequest, TaskListResponse, DataIngestRequest
from app.api.users.models import User
from app.core.dependencies import verify_resource_owner
from app.core.timezone import now
from app.db.database import async_session_maker, spider_async_engine
from config import settings

logger = logging.getLogger(__name__)

# ── 时间格式常量 ──
_TIME_FMT = "%Y-%m-%d %H:%M:%S"


async def get_all_tasks(
    status_filter: str | None,
    spider_id: int | None,
    task_id: str | None,
    start_time: str | None,
    end_time: str | None,
    skip: int,
    limit: int,
    session: AsyncSession,
) -> TaskListResponse:
    """
    带分页和条件过滤的任务列表查询。

    :param status_filter: 状态筛选（running/pending/success/error 等）
    :param spider_id: 按爬虫 ID 筛选
    :param task_id: 按任务 ID 精确匹配
    :param start_time: 创建时间起始（YYYY-MM-DD HH:MM:SS）
    :param end_time: 创建时间截止（YYYY-MM-DD HH:MM:SS）
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 异步数据库会话
    :return: TaskListResponse(items=任务列表, total=总数)
    :raises HTTPException: 400 — 时间格式错误；500 — 查询失败
    """
    try:
        query = select(SpiderTask).where(SpiderTask.is_deleted == False)
        count_query = select(func.count()).select_from(SpiderTask).where(SpiderTask.is_deleted == False)

        # 应用筛选条件
        if status_filter:
            query = query.where(SpiderTask.status == status_filter)
            count_query = count_query.where(SpiderTask.status == status_filter)
        if spider_id:
            query = query.where(SpiderTask.spider_id == spider_id)
            count_query = count_query.where(SpiderTask.spider_id == spider_id)
        if task_id:
            query = query.where(SpiderTask.task_id == task_id)
            count_query = count_query.where(SpiderTask.task_id == task_id)

        # 时间筛选：asyncpg 要求传 datetime 对象
        query, count_query = _apply_time_filter(query, count_query, start_time, end_time)

        count_result = await session.execute(count_query)
        total = count_result.scalar_one()

        query = query.order_by(desc(SpiderTask.id)).offset(skip).limit(limit)
        result = await session.execute(query)
        items = result.scalars().all()

        return TaskListResponse(items=items, total=total)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch task list: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch task list: {str(e)}",
        )


def _apply_time_filter(query, count_query, start_time: str | None, end_time: str | None):
    """
    将时间范围筛选条件应用到查询语句上。

    asyncpg 对参数类型推断严格，必须传 datetime 对象而非字符串，
    否则会绑定为 VARCHAR 导致与 TIMESTAMP 列比较时类型错误。

    :param query: 数据查询语句
    :param count_query: 计数查询语句
    :param start_time: 起始时间字符串（YYYY-MM-DD HH:MM:SS），可选
    :param end_time: 截止时间字符串（YYYY-MM-DD HH:MM:SS），可选
    :return: (query, count_query) 应用了时间条件的查询语句元组
    :raises HTTPException: 400 — 时间格式错误
    """
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
    return query, count_query


async def stop_task(
    task_id: str,
    operator: User,
    redis: Redis,
    session: AsyncSession,
) -> dict:
    """
    强制停止运行中的任务。

    1. 校验任务存在性和状态（仅 running/pending 可停止）
    2. 在数据库中将状态直接置为 cancelled
    3. 通过 Redis 下发 kill signal，通知 Worker 杀掉实际子进程

    :param task_id: 任务 UUID
    :param operator: 当前操作者
    :param redis: Redis 客户端
    :param session: 异步数据库会话
    :return: {"task_id": 任务 ID}
    :raises HTTPException: 404 — 任务不存在；400 — 任务状态不可停止
    """
    task = await _get_task_or_404(task_id, session)

    # 校验所有权
    spider = await session.get(Spider, task.spider_id)
    if spider:
        verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    if task.status not in ("running", "pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task is already in '{task.status}' state, cannot stop",
        )

    # 更新数据库状态
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

    # 通过 Redis 通知 Worker 杀进程
    await _send_kill_signal(task_id, redis)

    return {"task_id": task_id}


async def _get_task_or_404(task_id: str, session: AsyncSession) -> SpiderTask:
    """
    根据 task_id 查询任务，不存在或已删除则抛出 404。

    :param task_id: 任务 UUID
    :param session: 异步数据库会话
    :return: SpiderTask ORM 对象
    :raises HTTPException: 404 — 任务不存在或已删除
    """
    result = await session.execute(
        select(SpiderTask).where(SpiderTask.task_id == task_id)
    )
    task = result.scalars().first()
    if not task or task.is_deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def _send_kill_signal(task_id: str, redis: Redis) -> None:
    """
    通过 Redis 向 Worker 发送 kill 信号。

    Redis 异常不应阻断已完成的数据库更新流程。

    :param task_id: 任务 UUID
    :param redis: Redis 客户端
    :return: None
    """
    try:
        kill_key = f"task:kill:{task_id}"
        await redis.set(kill_key, "1", ex=120)

        channel = f"log:channel:{task_id}"
        await redis.publish(channel, "[SYSTEM: Kill signal sent to worker...]")

        logger.info(f"Kill signal sent for task {task_id}")
    except RedisError as e:
        logger.warning(f"Redis error when sending kill signal for task {task_id}: {e}")


async def delete_task(
    task_id: str,
    operator: User,
    redis: Redis,
    session: AsyncSession,
) -> dict:
    """
    软删除指定任务记录（标记 is_deleted = True），并清理 Redis 残留数据。

    :param task_id: 任务 UUID
    :param operator: 当前操作者
    :param redis: Redis 客户端
    :param session: 异步数据库会话
    :return: {"task_id": 任务 ID}
    :raises HTTPException: 404 — 任务不存在；400 — 不可删除 running 任务
    """
    result = await session.execute(
        select(SpiderTask).where(SpiderTask.task_id == task_id)
    )
    task = result.scalars().first()

    if task:
        spider = await session.get(Spider, task.spider_id)
        if spider:
            verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    if not task or task.is_deleted:
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

    # 清理 Redis 残留数据
    await _clean_redis_residual(task_id, redis)

    return {"task_id": task_id}


async def _clean_redis_residual(task_id: str, redis: Redis) -> None:
    """
    清理任务在 Redis 中可能残留的未处理数据缓存。

    包括 task:status:{task_id}:* 系列键和 kill signal 键。
    Redis 清理失败不应阻断删除流程。

    :param task_id: 任务 UUID
    :param redis: Redis 客户端
    :return: None
    """
    try:
        async for key in redis.scan_iter(match=f"task:status:{task_id}:*", count=100):
            await redis.delete(key)
        await redis.delete(f"task:kill:{task_id}")
        logger.info(f"Redis residual keys cleaned for task {task_id}")
    except RedisError as e:
        logger.warning(f"Failed to clean Redis residual data for task {task_id}: {e}")


async def get_task_logs(
    task_id: str,
    skip: int,
    limit: int,
    session: AsyncSession,
) -> list[dict]:
    """
    从数据库中查询指定任务的历史日志记录（按时间升序）。

    如果没有日志记录但任务有 error_detail，则补充一条系统日志。

    :param task_id: 任务 UUID
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 异步数据库会话
    :return: 日志记录列表 [{"id": ..., "content": ..., "created_at": ...}]
    :raises HTTPException: 500 — 查询失败
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
            log_items = await _append_error_detail_log(task_id, log_items, session)

        return log_items
    except Exception as e:
        logger.error(f"Failed to fetch logs for task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch task logs: {str(e)}",
        )


async def _append_error_detail_log(
    task_id: str,
    log_items: list[dict],
    session: AsyncSession,
) -> list[dict]:
    """
    当任务无日志但有 error_detail 时，补充一条系统错误日志。

    :param task_id: 任务 UUID
    :param log_items: 当前的日志列表（空列表）
    :param session: 异步数据库会话
    :return: 可能追加了系统错误日志的日志列表
    """
    task_result = await session.execute(
        select(SpiderTask).where(SpiderTask.task_id == task_id)
    )
    task_obj = task_result.scalars().first()
    if task_obj and task_obj.error_detail:
        log_items.append({
            "id": 0,
            "content": f"[SYSTEM ERROR]: {task_obj.error_detail}",
            "created_at": str(task_obj.finished_at or task_obj.updated_at or now()),
        })
    return log_items


async def get_task_data(
    task_id: str,
    table_name: str | None,
    skip: int,
    limit: int,
    session: AsyncSession,
) -> dict:
    """
    查询指定任务采集的数据记录（存储在 Spider 数据库的动态表中）。

    :param task_id: 任务 UUID
    :param table_name: 数据表名（不传则使用任务关联的 spider_name）
    :param skip: 跳过记录数
    :param limit: 返回记录数上限（最大 500）
    :param session: 异步数据库会话
    :return: {"items": [...], "total": N, "table_name": ..., "skip": ..., "limit": ...}
    :raises HTTPException: 404 — 任务不存在；500 — 查询失败
    """
    limit = min(limit, 500)

    try:
        # 查询任务信息
        task_result = await session.execute(
            select(SpiderTask).where(SpiderTask.task_id == task_id)
        )
        task_obj = task_result.scalars().first()
        if not task_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found",
            )

        target_table = table_name or task_obj.spider_name
        if not target_table:
            return {"items": [], "total": 0, "table_name": None}

        # 使用爬虫数据专属引擎查询
        items, total = await _query_spider_data(task_id, target_table, skip, limit)

        return {
            "items": items,
            "total": total,
            "table_name": target_table,
            "skip": skip,
            "limit": limit,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch data for task {task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch task data: {str(e)}",
        )


async def _query_spider_data(
    task_id: str,
    target_table: str,
    skip: int,
    limit: int,
) -> tuple[list[dict], int]:
    """
    在 Spider 数据库中查询指定表的采集数据。

    :param task_id: 任务 UUID
    :param target_table: 目标数据表名
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :return: (数据列表, 总数) 元组
    """
    async with spider_async_engine.connect() as spider_conn:
        # 检查表是否存在
        check_table_sql = text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :name"
        )
        result = await spider_conn.execute(check_table_sql, {"name": target_table})
        if result.scalar_one_or_none() is None:
            return [], 0

        # 查询总数
        count_sql = text(f'SELECT COUNT(*) FROM "{target_table}" WHERE "_task_id" = :task_id')
        count_result = await spider_conn.execute(count_sql, {"task_id": task_id})
        total = count_result.scalar() or 0

        # 分页查询数据
        data_sql = text(
            f'SELECT "_id", "_task_id", "_data", "_created_at" '
            f'FROM "{target_table}" '
            f'WHERE "_task_id" = :task_id '
            f'ORDER BY "_id" DESC '
            f'OFFSET :skip LIMIT :limit'
        )
        data_result = await spider_conn.execute(
            data_sql,
            {"task_id": task_id, "skip": skip, "limit": limit},
        )
        rows = data_result.fetchall()

    items = [
        {
            "id": row[0],
            "task_id": row[1],
            "data": row[2],
            "created_at": str(row[3]) if row[3] else None,
        }
        for row in rows
    ]

    return items, total


async def ingest_data(
    task_id: str,
    body: DataIngestRequest,
    redis: Redis,
) -> dict:
    """
    高并发数据接入网关：将爬虫采集数据快速压入 Redis 队列。

    接口内部严禁任何 Postgres 同步操作，响应时间需维持在毫秒级。

    :param task_id: 关联的任务 UUID
    :param body: 数据接入请求体（包含 table_name 和 data）
    :param redis: Redis 客户端
    :return: {"task_id": ..., "count": 数据条数}
    :raises HTTPException: 503 — Redis 异常；400 — 序列化失败
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

    return {"task_id": task_id, "count": len(body.data)}


async def run_task(
    request: TaskRequest,
    redis: Redis,
) -> dict:
    """
    根据传入的 spider_id 查询爬虫配置，构建 payload 并下发到 Redis 队列。

    :param request: 任务下发请求体
    :param redis: Redis 客户端
    :return: {"task_id": ..., "queues": 目标队列列表}
    :raises HTTPException: 404 — 爬虫不存在；500 — 下发失败
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(Spider).where(Spider.id == request.spider_id, Spider.is_deleted == False)
        )
        spider = result.scalars().first()

    if not spider or spider.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Spider {request.spider_id} not found.",
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

        # 创建持久化任务记录
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

        # 路由到正确的 Redis 队列
        target_queues = await _dispatch_to_queues(request, task_data, redis)

        return {"task_id": request.task_id, "queues": target_queues}

    except RedisError as e:
        logger.error(f"Redis error when dispatching task {request.task_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to dispatch task due to Redis error: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Unexpected error dispatching task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred while dispatching task",
        )


async def _dispatch_to_queues(
    request: TaskRequest,
    task_data: str,
    redis: Redis,
) -> list[str]:
    """
    将任务数据推送到目标 Redis 队列，并设置初始状态。

    :param request: 任务请求对象
    :param task_data: 序列化后的任务 JSON 字符串
    :param redis: Redis 客户端
    :return: 目标队列键名列表
    """
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

    return target_queues
