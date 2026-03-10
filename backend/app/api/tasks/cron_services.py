"""
Cron 定时调度业务逻辑层

将定时任务的添加、查询、删除、切换和修改的业务逻辑从 router.py 分离出来。
依赖 APScheduler 和 Redis JobStore。
"""
import uuid
import logging

from fastapi import HTTPException, status
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.spiders.models import Spider
from app.api.tasks.cron_schemas import CronTaskCreate, CronTaskResponse, CronTaskUpdate, CronTaskToggle
from app.api.users.models import User
from app.core.scheduler import scheduler
from app.db.database import async_session_maker
from app.worker.cron_jobs import dispatch_scheduled_task

logger = logging.getLogger(__name__)


def _generate_job_id() -> str:
    """
    生成唯一的定时任务 Job ID。

    :return: 格式为 "schedule-{8位十六进制}" 的唯一 ID
    """
    return f"schedule-{uuid.uuid4().hex[:8]}"


async def _get_spider_name(spider_id: int) -> str:
    """
    查询 Spider 名称，用于在 Cron 响应中展示。

    :param spider_id: 爬虫主键 ID
    :return: Spider 名称，查不到则返回空字符串
    """
    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(Spider.name).where(Spider.id == spider_id)
            )
            row = result.scalar_one_or_none()
            return row or ""
    except Exception:
        return ""


async def _batch_get_spider_names(spider_ids: set[int]) -> dict[int, str]:
    """
    批量查询多个 Spider 的名称映射。

    :param spider_ids: 爬虫 ID 集合
    :return: {spider_id: spider_name} 的映射字典
    """
    if not spider_ids:
        return {}

    spider_name_map: dict[int, str] = {}
    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(Spider.id, Spider.name).where(Spider.id.in_(spider_ids))
            )
            for row in result:
                spider_name_map[row[0]] = row[1]
    except Exception as e:
        logger.warning(f"Failed to fetch spider names: {e}")

    return spider_name_map


def _build_cron_response(job) -> CronTaskResponse:
    """
    从 APScheduler Job 对象构建 CronTaskResponse（不含 spider_name）。

    :param job: APScheduler Job 对象
    :return: CronTaskResponse schema 实例
    """
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


async def add_cron_task(
    body: CronTaskCreate,
    operator: User,
) -> CronTaskResponse:
    """
    向 APScheduler 添加一个基于 Cron 表达式的定时任务。

    :param body: 定时任务创建请求体（包含 cron_expr、spider_id 等）
    :param operator: 当前操作者
    :return: CronTaskResponse 创建结果
    :raises HTTPException: 400 — Cron 表达式无效或添加失败
    """
    try:
        trigger = CronTrigger.from_crontab(body.cron_expr)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的 Cron 表达式: {str(e)}",
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
                "cron_expr": body.cron_expr,
                "description": body.description,
                "owner_id": operator.id,
            },
            replace_existing=True,
        )

        if not body.enabled:
            job.pause()

        spider_name = await _get_spider_name(body.spider_id)

        logger.info(f"Cron task {job_id} added for spider {body.spider_id} with expr {body.cron_expr}")

        resp = _build_cron_response(job)
        resp.spider_name = spider_name
        resp.enabled = body.enabled
        return resp

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add cron task: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"添加定时任务失败: {str(e)}",
        )


async def get_cron_tasks() -> list[CronTaskResponse]:
    """
    获取 APScheduler 中所有定时任务列表（含暂停的）。

    自动过滤系统内置任务（如 daily_image_prune），仅返回有 spider_id 的业务任务。

    :return: CronTaskResponse 列表
    :raises HTTPException: 500 — 查询失败
    """
    try:
        jobs = scheduler.get_jobs()

        # 批量查询所有 spider_id 对应的名称
        spider_ids: set[int] = set()
        for job in jobs:
            kwargs = job.kwargs or {}
            sid = kwargs.get("spider_id")
            if sid is not None:
                spider_ids.add(sid)

        spider_name_map = await _batch_get_spider_names(spider_ids)

        response_list: list[CronTaskResponse] = []
        for job in jobs:
            kwargs = job.kwargs or {}
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
            detail="获取定时任务列表失败",
        )


async def delete_cron_task(job_id: str) -> dict:
    """
    根据 Job ID 删除 APScheduler 中的定时任务。

    :param job_id: APScheduler 任务 ID
    :return: {"message": ..., "job_id": ...}
    :raises HTTPException: 404 — 任务不存在
    """
    try:
        job = scheduler.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="定时任务未找到或删除失败",
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
            detail=f"定时任务未找到或删除失败: {str(e)}",
        )


async def toggle_cron_task(
    job_id: str,
    body: CronTaskToggle,
) -> CronTaskResponse:
    """
    暂停或恢复指定的定时任务。

    :param job_id: APScheduler 任务 ID
    :param body: 包含 enabled 字段的请求体
    :return: CronTaskResponse 更新后的任务状态
    :raises HTTPException: 404 — 任务不存在；400 — 切换失败
    """
    try:
        job = scheduler.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"定时任务 {job_id} 不存在",
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
            detail=f"切换定时任务失败: {str(e)}",
        )


async def update_cron_task(
    job_id: str,
    body: CronTaskUpdate,
) -> CronTaskResponse:
    """
    修改已有定时任务的属性（爬虫关联、cron 表达式、描述、节点等）。

    :param job_id: APScheduler 任务 ID
    :param body: 定时任务更新请求体（所有字段可选）
    :return: CronTaskResponse 更新后的任务状态
    :raises HTTPException: 404 — 任务不存在；400 — Cron 表达式无效或更新失败
    """
    try:
        job = scheduler.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"定时任务 {job_id} 不存在",
            )

        current_kwargs = dict(job.kwargs or {})

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
                    detail=f"无效的 Cron 表达式: {str(e)}",
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
            detail=f"更新定时任务失败: {str(e)}",
        )
