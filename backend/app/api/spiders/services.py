"""
爬虫管理业务逻辑层

将 router 中的业务逻辑全部迁移至此，router 仅负责接口响应和参数解析。
包含爬虫 CRUD、ZIP 文件管理、任务触发等全部核心逻辑。
"""
import json
import uuid
import logging
from typing import List

from fastapi import HTTPException, UploadFile
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.spiders.models import Spider
from app.api.spiders.schemas import SpiderCreate, SpiderUpdate, SpiderFileSave, SpiderFileCreate, SpiderFileDelete, SpiderRunRequest
from app.api.tasks.models import SpiderTask, TaskLog
from app.api.users.models import User
from app.core.dependencies import verify_resource_owner
from app.core.enums import UserRole
from app.core.storage.minio_client import minio_manager
from app.core.storage.zip_helper import (
    check_protected_file, validate_file_path,
    download_zip_bytes, list_files as zip_list_files, read_file as zip_read_file,
    update_file as zip_update_file, add_file as zip_add_file, delete_file as zip_delete_file,
    upload_zip_bytes,
)
from app.core.timezone import now
from config import settings

logger = logging.getLogger(__name__)


async def upload_zip(file: UploadFile) -> str:
    """
    上传爬虫代码 ZIP 包到 MinIO。

    :param file: 上传的文件对象，必须为 .zip 格式
    :return: MinIO 中的对象路径（source_url）
    :raises HTTPException: 400 — 文件格式不是 .zip；500 — 上传失败
    """
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are allowed")

    object_name = f"spiders/{uuid.uuid4().hex[:8]}/{file.filename}"
    try:
        minio_manager.upload_stream(
            object_name=object_name,
            file_data=file.file,
            length=-1,
        )
        logger.info(f"File {file.filename} uploaded to MinIO as {object_name}.")
        return object_name
    except Exception as e:
        logger.error(f"MinIO upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload file")


async def create_spider(
    spider_in: SpiderCreate,
    operator: User,
    session: AsyncSession,
) -> Spider:
    """
    创建一个新的爬虫记录。

    :param spider_in: 爬虫创建请求 schema
    :param operator: 当前操作者用户对象
    :param session: 异步数据库会话
    :return: 创建后的 Spider ORM 对象
    """
    target_nodes_str = json.dumps(spider_in.target_nodes) if spider_in.target_nodes else None
    db_spider = Spider(
        name=spider_in.name,
        description=spider_in.description,
        project_id=spider_in.project_id,
        source_type=spider_in.source_type,
        source_url=spider_in.source_url,
        language=spider_in.language or "python:3.11-slim",
        command=spider_in.command,
        target_nodes=target_nodes_str,
        owner_id=operator.id,
    )
    session.add(db_spider)
    await session.flush()
    await session.commit()
    await session.refresh(db_spider)
    return db_spider


async def get_spider_list(
    skip: int,
    limit: int,
    session: AsyncSession,
) -> List[Spider]:
    """
    获取未删除的爬虫列表（分页）。

    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 异步数据库会话
    :return: Spider ORM 对象列表
    """
    stmt = (
        select(Spider)
        .where(Spider.is_deleted == False)
        .order_by(Spider.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_spider_by_id(
    spider_id: int,
    session: AsyncSession,
) -> Spider:
    """
    根据 ID 获取单个爬虫，校验存在性和软删除状态。

    :param spider_id: 爬虫主键 ID
    :param session: 异步数据库会话
    :return: Spider ORM 对象
    :raises HTTPException: 404 — 爬虫不存在或已删除
    """
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")
    return spider


def _ensure_minio_type(spider: Spider) -> None:
    """
    校验爬虫是否为 MINIO 类型（仅 MINIO 类型支持在线代码操作）。

    :param spider: Spider ORM 对象
    :return: None
    :raises HTTPException: 400 — 非 MINIO 类型
    """
    if spider.source_type != "MINIO":
        raise HTTPException(status_code=400, detail="仅支持 MINIO 类型爬虫的代码查看")


async def update_spider(
    spider_id: int,
    spider_in: SpiderUpdate,
    operator: User,
    session: AsyncSession,
) -> Spider:
    """
    更新指定爬虫的信息。

    :param spider_id: 爬虫主键 ID
    :param spider_in: 爬虫更新请求 schema（仅包含需更新的字段）
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: 更新后的 Spider ORM 对象
    :raises HTTPException: 404 — 爬虫不存在；403 — 无权操作
    """
    spider = await get_spider_by_id(spider_id, session)
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    update_data = spider_in.dict(exclude_unset=True)
    if "target_nodes" in update_data:
        update_data["target_nodes"] = (
            json.dumps(update_data["target_nodes"]) if update_data["target_nodes"] else None
        )

    for key, value in update_data.items():
        setattr(spider, key, value)
    spider.updated_at = now()

    session.add(spider)
    await session.commit()
    await session.refresh(spider)
    return spider


async def delete_spider(
    spider_id: int,
    operator: User,
    session: AsyncSession,
) -> None:
    """
    软删除指定爬虫（标记 is_deleted = True）。

    :param spider_id: 爬虫主键 ID
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: None
    :raises HTTPException: 404 — 爬虫不存在；403 — 无权操作
    """
    spider = await get_spider_by_id(spider_id, session)
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    spider.is_deleted = True
    session.add(spider)
    await session.commit()


# ── ZIP 文件管理 ──────────────────────────────────────────────────────────


async def list_spider_files(
    spider_id: int,
    session: AsyncSession,
) -> list[str]:
    """
    列出爬虫 ZIP 包内的文件列表（过滤 __pycache__ 等）。

    :param spider_id: 爬虫主键 ID
    :param session: 异步数据库会话
    :return: ZIP 包内的文件路径列表
    :raises HTTPException: 404 — 爬虫不存在；400 — 非 MINIO 类型或 ZIP 损坏
    """
    spider = await get_spider_by_id(spider_id, session)
    _ensure_minio_type(spider)

    zip_bytes = download_zip_bytes(spider.source_url, spider_id)
    return zip_list_files(zip_bytes)


async def read_spider_file(
    spider_id: int,
    path: str,
    session: AsyncSession,
) -> dict[str, str]:
    """
    读取爬虫 ZIP 包内指定文件的内容。

    :param spider_id: 爬虫主键 ID
    :param path: ZIP 包内的文件路径
    :param session: 异步数据库会话
    :return: {"path": 文件路径, "content": 文件内容}
    :raises HTTPException: 404 — 爬虫或文件不存在
    """
    spider = await get_spider_by_id(spider_id, session)
    _ensure_minio_type(spider)

    zip_bytes = download_zip_bytes(spider.source_url, spider_id)
    content = zip_read_file(zip_bytes, path)
    return {"path": path, "content": content}


async def save_spider_file(
    spider_id: int,
    body: SpiderFileSave,
    operator: User,
    session: AsyncSession,
) -> None:
    """
    保存修改后的文件到爬虫 ZIP 包（替换已有文件内容）。

    :param spider_id: 爬虫主键 ID
    :param body: 包含 path 和 content 的请求体
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: None
    :raises HTTPException: 403 — 受保护文件或无权操作；404 — 爬虫不存在
    """
    check_protected_file(body.path)

    spider = await get_spider_by_id(spider_id, session)
    _ensure_minio_type(spider)
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    zip_bytes = download_zip_bytes(spider.source_url, spider_id)
    new_zip = zip_update_file(zip_bytes, body.path, body.content)
    upload_zip_bytes(spider.source_url, new_zip, spider_id)

    spider.updated_at = now()
    session.add(spider)
    await session.commit()


async def create_spider_file(
    spider_id: int,
    body: SpiderFileCreate,
    operator: User,
    session: AsyncSession,
) -> list[str]:
    """
    在爬虫 ZIP 包内新增文件，返回更新后的文件列表。

    :param spider_id: 爬虫主键 ID
    :param body: 包含 path 和 content 的请求体
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: 更新后的 ZIP 包内文件路径列表
    :raises HTTPException: 403 — 受保护文件或无权操作；409 — 文件已存在
    """
    check_protected_file(body.path)
    validate_file_path(body.path)

    spider = await get_spider_by_id(spider_id, session)
    _ensure_minio_type(spider)
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    zip_bytes = download_zip_bytes(spider.source_url, spider_id)
    new_zip = zip_add_file(zip_bytes, body.path, body.content)
    upload_zip_bytes(spider.source_url, new_zip, spider_id)

    spider.updated_at = now()
    session.add(spider)
    await session.commit()

    return zip_list_files(new_zip)


async def delete_spider_file(
    spider_id: int,
    body: SpiderFileDelete,
    operator: User,
    session: AsyncSession,
) -> list[str]:
    """
    从爬虫 ZIP 包内删除指定文件，返回更新后的文件列表。

    :param spider_id: 爬虫主键 ID
    :param body: 包含 path 的请求体
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: 更新后的 ZIP 包内文件路径列表
    :raises HTTPException: 403 — 受保护文件或无权操作；404 — 文件不存在
    """
    check_protected_file(body.path)
    validate_file_path(body.path)

    spider = await get_spider_by_id(spider_id, session)
    _ensure_minio_type(spider)
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    zip_bytes = download_zip_bytes(spider.source_url, spider_id)
    new_zip = zip_delete_file(zip_bytes, body.path)
    upload_zip_bytes(spider.source_url, new_zip, spider_id)

    spider.updated_at = now()
    session.add(spider)
    await session.commit()

    return zip_list_files(new_zip)


# ── 爬虫运行 ──────────────────────────────────────────────────────────


async def run_spider(
    spider_id: int,
    body: SpiderRunRequest,
    operator: User,
    session: AsyncSession,
    redis: Redis,
) -> str:
    """
    构建任务 payload 并推送到 Redis 队列，同时创建 SpiderTask 数据库记录。

    :param spider_id: 爬虫主键 ID
    :param body: 运行请求体（包含 target_nodes、timeout_seconds）
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :param redis: Redis 客户端
    :return: 新创建的任务 ID
    :raises HTTPException: 404 — 爬虫不存在；500 — 推送失败
    """
    spider = await get_spider_by_id(spider_id, session)
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    task_id = str(uuid.uuid4())

    payload = {
        "task_id": task_id,
        "project_id": spider.project_id,
        "source_type": spider.source_type,
        "source_url": spider.source_url,
        "language": spider.language or "default",
        "script_path": spider.command,
        "timeout_seconds": body.timeout_seconds or 3600,
    }

    db_task = SpiderTask(
        task_id=task_id,
        spider_id=spider_id,
        spider_name=spider.name,
        status="pending",
        command=spider.command,
    )
    session.add(db_task)
    await session.commit()

    try:
        task_json = json.dumps(payload)
        target_queues: list[str] = []

        if body.target_nodes:
            for node_id in body.target_nodes:
                target_queues.append(f"{settings.NODE_QUEUE_PREFIX}{node_id}")
        else:
            target_queues.append(settings.PUBLIC_QUEUE_KEY)

        for queue_key in target_queues:
            await redis.rpush(queue_key, task_json)

        logger.info("Spider %d task %s pushed to queues: %s", spider_id, task_id, target_queues)
        return task_id
    except Exception as exc:
        logger.error("Failed to push spider run task to Redis: %s", exc)
        raise HTTPException(status_code=500, detail="推送任务失败，请检查 Redis 连接")


# ── 爬虫任务查询 ──────────────────────────────────────────────────────


async def list_spider_tasks(
    spider_id: int,
    skip: int,
    limit: int,
    operator: User,
    session: AsyncSession,
) -> list[SpiderTask]:
    """
    获取指定爬虫的任务执行历史（分页）。

    :param spider_id: 爬虫主键 ID
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: SpiderTask ORM 对象列表
    :raises HTTPException: 404 — 爬虫不存在；403 — 无权查看
    """
    spider = await get_spider_by_id(spider_id, session)

    if operator.role != UserRole.admin:
        verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    stmt = (
        select(SpiderTask)
        .where(SpiderTask.spider_id == spider_id, SpiderTask.is_deleted == False)
        .order_by(SpiderTask.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_spider_task_logs(
    spider_id: int,
    task_id: str,
    skip: int,
    limit: int,
    operator: User,
    session: AsyncSession,
) -> list[TaskLog]:
    """
    获取指定爬虫任务的日志记录。

    :param spider_id: 爬虫主键 ID
    :param task_id: 任务 UUID
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: TaskLog ORM 对象列表
    :raises HTTPException: 404 — 爬虫不存在；403 — 无权查看
    """
    spider = await get_spider_by_id(spider_id, session)
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    result = await session.execute(
        select(TaskLog)
        .where(TaskLog.task_id == task_id)
        .order_by(TaskLog.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_spider_status(
    spider_id: int,
    operator: User,
    session: AsyncSession,
) -> dict:
    """
    获取爬虫最近一次任务的状态，若无任务返回 idle。

    :param spider_id: 爬虫主键 ID
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: {"status": ..., "task_id": ..., "started_at": ..., "finished_at": ...}
    :raises HTTPException: 404 — 爬虫不存在；403 — 无权查看
    """
    spider = await get_spider_by_id(spider_id, session)
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    result = await session.execute(
        select(SpiderTask)
        .where(SpiderTask.spider_id == spider_id)
        .order_by(SpiderTask.created_at.desc())
        .limit(1)
    )
    latest_task = result.scalars().first()
    if not latest_task:
        return {"status": "idle", "task_id": None}

    return {
        "status": latest_task.status,
        "task_id": latest_task.task_id,
        "started_at": latest_task.started_at.isoformat() if latest_task.started_at else None,
        "finished_at": latest_task.finished_at.isoformat() if latest_task.finished_at else None,
    }
