"""
爬虫管理路由 — 全异步
"""
import json
import uuid
import logging
from typing import List
from datetime import datetime

from app.core.timezone import now

from fastapi import APIRouter, Depends, Query, Path, HTTPException, status, UploadFile, File, Request, BackgroundTasks
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.spiders.models import Spider
from app.api.spiders.schemas import (
    SpiderCreate, SpiderUpdate, SpiderOut, SpiderRunRequest, SpiderFileSave,
    SpiderFileCreate, SpiderFileDelete,
    SpiderTaskOut, TaskLogOut,
)
from app.api.tasks.models import SpiderTask, TaskLog
from app.api.users.models import User
from app.db.database import get_async_session
from app.core.redis import get_redis
from app.core.schemas.api_response import ApiResponse
from app.core.storage.minio_client import minio_manager
from app.core.dependencies import require_viewer, require_developer, verify_resource_owner
from app.core.audit.service import audit_log
from app.core.enums import UserRole
from config import settings

import zipfile
import io

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/upload", response_model=ApiResponse, summary="上传爬虫代码 ZIP 包")
async def upload_spider_zip(file: UploadFile = File(...)):
    if not file.filename.endswith('.zip'):
        raise HTTPException(status_code=400, detail="Only .zip files are allowed")

    object_name = f"spiders/{uuid.uuid4().hex[:8]}/{file.filename}"
    try:
        # Uploading using MinIO client
        minio_manager.upload_stream(
            object_name=object_name,
            file_data=file.file,
            length=-1
        )
        logger.info(f"File {file.filename} uploaded to MinIO as {object_name}.")
        return ApiResponse.success(data={"source_url": object_name})
    except Exception as e:
        logger.error(f"MinIO upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload file")


@router.post("", response_model=ApiResponse[SpiderOut], summary="创建爬虫")
@audit_log(action="CREATE", resource_type="spider")
async def create_spider(
    spider_in: SpiderCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
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
    await session.flush()  # 获取自增 id，用于审计日志

    await session.commit()
    await session.refresh(db_spider)
    return ApiResponse.success(data=db_spider)


@router.get("", response_model=ApiResponse[List[SpiderOut]], summary="获取爬虫列表")
async def read_spiders(
    skip: int = Query(0),
    limit: int = Query(100),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    stmt = select(Spider).where(Spider.is_deleted == False).order_by(Spider.created_at.desc()).offset(skip).limit(limit)

    result = await session.execute(stmt)
    spiders = result.scalars().all()
    return ApiResponse.success(data=list(spiders))


@router.get("/{spider_id}", response_model=ApiResponse[SpiderOut], summary="获取指定爬虫")
async def read_spider(
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")
    return ApiResponse.success(data=spider)


@router.post("/{spider_id}/update", response_model=ApiResponse[SpiderOut], summary="更新爬虫信息")
@audit_log(action="UPDATE", resource_type="spider")
async def update_spider(
    spider_in: SpiderUpdate,
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")

    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    # 快照更新前的原值
    original_snapshot = json.dumps(
        {k: getattr(spider, k, None) for k in spider_in.dict(exclude_unset=True)},
        default=str, ensure_ascii=False,
    )

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
    return ApiResponse.success(data=spider)


@router.post("/{spider_id}/delete", response_model=ApiResponse, summary="删除爬虫")
@audit_log(action="DELETE", resource_type="spider")
async def delete_spider(
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):

    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")

    # 校验所有权
    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    spider.is_deleted = True
    session.add(spider)

    await session.commit()
    return ApiResponse.success(message="Spider deleted successfully")



# ── 受保护的环境依赖文件集合，禁止在线修改 ──
_PROTECTED_FILES = {
    "requirements.txt", "Pipfile", "Pipfile.lock",
    "pyproject.toml", "poetry.lock", "setup.py", "setup.cfg",
    "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
}


def _check_protected_file(path: str) -> None:
    """如果目标路径命中受保护的依赖文件，抛出 403 异常"""
    # 取路径中的文件名部分进行匹配（兼容子目录中的同名文件）
    filename = path.rsplit("/", maxsplit=1)[-1] if "/" in path else path
    if filename in _PROTECTED_FILES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"禁止在线修改环境依赖文件: {filename}",
        )


# 不在文件树中展示的路径模式
_SKIP_PATTERNS = {"__pycache__", ".pyc", ".git", ".DS_Store", "__MACOSX"}


def _should_skip(path: str) -> bool:
    """判断 ZIP 内路径是否需要跳过"""
    parts = path.split("/")
    return any(p for p in parts if any(skip in p for skip in _SKIP_PATTERNS))


@router.get("/{spider_id}/files", response_model=ApiResponse, summary="列出爬虫 ZIP 包内的文件列表")
async def list_spider_files(
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
):
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")
    if spider.source_type != "MINIO":
        raise HTTPException(status_code=400, detail="仅支持 MINIO 类型爬虫的代码查看")

    try:
        zip_bytes = minio_manager.download_object(spider.source_url)
    except RuntimeError as e:
        logger.error(f"Failed to download ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="下载 ZIP 文件失败")

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            files = [
                info.filename
                for info in zf.infolist()
                if not info.is_dir() and not _should_skip(info.filename)
            ]
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    return ApiResponse.success(data=files)


@router.get("/{spider_id}/file", response_model=ApiResponse, summary="读取爬虫 ZIP 包内指定文件内容")
async def read_spider_file(
    spider_id: int = Path(...),
    path: str = Query(..., description="ZIP 包内的文件路径"),
    session: AsyncSession = Depends(get_async_session),
):
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")
    if spider.source_type != "MINIO":
        raise HTTPException(status_code=400, detail="仅支持 MINIO 类型爬虫的代码查看")

    try:
        zip_bytes = minio_manager.download_object(spider.source_url)
    except RuntimeError as e:
        logger.error(f"Failed to download ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="下载 ZIP 文件失败")

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            if path not in zf.namelist():
                raise HTTPException(status_code=404, detail=f"文件 {path} 不存在于 ZIP 包中")
            raw = zf.read(path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    # 尝试以 UTF-8 解码，失败则返回 Latin-1
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("latin-1")

    return ApiResponse.success(data={"path": path, "content": content})


@router.post("/{spider_id}/file", response_model=ApiResponse, summary="保存修改后的文件到爬虫 ZIP 包")
@audit_log(action="UPDATE", resource_type="spider")
async def save_spider_file(
    body: SpiderFileSave,
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    _check_protected_file(body.path)

    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")
    if spider.source_type != "MINIO":
        raise HTTPException(status_code=400, detail="仅支持 MINIO 类型爬虫的代码修改")

    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    try:
        zip_bytes = minio_manager.download_object(spider.source_url)
    except RuntimeError as e:
        logger.error(f"Failed to download ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="下载 ZIP 文件失败")

    # 读取原始 ZIP 所有文件，替换目标文件内容，重新打包
    try:
        old_zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zf:
            for item in old_zf.infolist():
                if item.filename == body.path:
                    new_zf.writestr(item, body.content.encode("utf-8"))
                else:
                    new_zf.writestr(item, old_zf.read(item.filename))
        old_zf.close()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    # 上传覆盖
    try:
        minio_manager.upload_bytes(spider.source_url, buf.getvalue())
    except RuntimeError as e:
        logger.error(f"Failed to upload modified ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="上传修改后的 ZIP 失败")

    # 更新时间戳
    spider.updated_at = now()
    session.add(spider)

    await session.commit()

    return ApiResponse.success(message="文件保存成功")


def _validate_file_path(path: str) -> None:
    """校验文件路径安全性，防止路径穿越"""
    if not path or path.startswith('/') or '..' in path.split('/'):
        raise HTTPException(status_code=400, detail="文件路径不合法")


@router.post("/{spider_id}/file/create", response_model=ApiResponse, summary="在爬虫 ZIP 包内新增文件")
@audit_log(action="CREATE", resource_type="spider_file")
async def create_spider_file(
    body: SpiderFileCreate,
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    _check_protected_file(body.path)
    _validate_file_path(body.path)

    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")
    if spider.source_type != "MINIO":
        raise HTTPException(status_code=400, detail="仅支持 MINIO 类型爬虫的代码修改")

    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    try:
        zip_bytes = minio_manager.download_object(spider.source_url)
    except RuntimeError as e:
        logger.error(f"Failed to download ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="下载 ZIP 文件失败")

    try:
        old_zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        # 检查文件是否已存在
        if body.path in old_zf.namelist():
            old_zf.close()
            raise HTTPException(status_code=409, detail=f"文件 {body.path} 已存在")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zf:
            for item in old_zf.infolist():
                new_zf.writestr(item, old_zf.read(item.filename))
            # 写入新文件
            new_zf.writestr(body.path, body.content.encode("utf-8"))
        old_zf.close()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    try:
        minio_manager.upload_bytes(spider.source_url, buf.getvalue())
    except RuntimeError as e:
        logger.error(f"Failed to upload modified ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="上传修改后的 ZIP 失败")

    spider.updated_at = now()
    session.add(spider)

    await session.commit()

    # 返回更新后的文件列表
    with zipfile.ZipFile(io.BytesIO(buf.getvalue()), "r") as zf:
        files = [
            info.filename
            for info in zf.infolist()
            if not info.is_dir() and not _should_skip(info.filename)
        ]

    return ApiResponse.success(data=files, message="文件创建成功")


@router.post("/{spider_id}/file/delete", response_model=ApiResponse, summary="从爬虫 ZIP 包内删除文件")
@audit_log(action="DELETE", resource_type="spider_file")
async def delete_spider_file(
    body: SpiderFileDelete,
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    _check_protected_file(body.path)
    _validate_file_path(body.path)

    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")
    if spider.source_type != "MINIO":
        raise HTTPException(status_code=400, detail="仅支持 MINIO 类型爬虫的代码修改")

    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    try:
        zip_bytes = minio_manager.download_object(spider.source_url)
    except RuntimeError as e:
        logger.error(f"Failed to download ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="下载 ZIP 文件失败")

    try:
        old_zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        if body.path not in old_zf.namelist():
            old_zf.close()
            raise HTTPException(status_code=404, detail=f"文件 {body.path} 不存在于 ZIP 包中")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zf:
            for item in old_zf.infolist():
                if item.filename != body.path:
                    new_zf.writestr(item, old_zf.read(item.filename))
        old_zf.close()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    try:
        minio_manager.upload_bytes(spider.source_url, buf.getvalue())
    except RuntimeError as e:
        logger.error(f"Failed to upload modified ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="上传修改后的 ZIP 失败")

    spider.updated_at = now()
    session.add(spider)

    await session.commit()

    # 返回更新后的文件列表
    with zipfile.ZipFile(io.BytesIO(buf.getvalue()), "r") as zf:
        files = [
            info.filename
            for info in zf.infolist()
            if not info.is_dir() and not _should_skip(info.filename)
        ]

    return ApiResponse.success(data=files, message="文件删除成功")


@router.post("/{spider_id}/run", response_model=ApiResponse, summary="触发爬虫运行")
@audit_log(action="RUN", resource_type="spider")
async def run_spider(
    body: SpiderRunRequest,
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    redis: Redis = Depends(get_redis),
    operator: User = Depends(require_developer),
):
    """
    将爬虫运行任务推送到 Redis 队列，并创建 SpiderTask 数据库记录。
    指定 target_nodes 时分发到各节点专属队列，否则投放公共队列。
    """
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")

    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    task_id = str(uuid.uuid4())

    # ── 构建 executor 所需的标准 payload ──
    payload = {
        "task_id": task_id,
        "project_id": spider.project_id,
        "source_type": spider.source_type,
        "source_url": spider.source_url,
        "language": spider.language or "default",   # 显式传递 default 触发 executor 容器模式
        "script_path": spider.command,          # executor 用 script_path 作为子进程命令
        "timeout_seconds": body.timeout_seconds or 3600,
    }

    # ── 创建持久化任务记录 ──
    db_task = SpiderTask(
        task_id=task_id,
        spider_id=spider_id,
        spider_name=spider.name,
        status="pending",
        command=spider.command,
    )
    session.add(db_task)
    await session.commit()

    # ── 路由到正确的 Redis 队列 ──

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
        return ApiResponse.success(data={"task_id": task_id}, message="任务已进入调度队列")
    except Exception as exc:
        logger.error("Failed to push spider run task to Redis: %s", exc)
        raise HTTPException(status_code=500, detail="推送任务失败，请检查 Redis 连接")



@router.get("/{spider_id}/tasks", response_model=ApiResponse[List[SpiderTaskOut]], summary="获取爬虫的任务历史")
async def list_spider_tasks(
    spider_id: int = Path(...),
    skip: int = Query(0),
    limit: int = Query(50),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")

    # 对于 admin 则不进行权限过滤，其他人需要 own
    if operator.role != UserRole.admin:
        verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    stmt = select(SpiderTask).where(SpiderTask.spider_id == spider_id, SpiderTask.is_deleted == False).order_by(SpiderTask.created_at.desc()).offset(skip).limit(limit)

    result = await session.execute(stmt)
    tasks = result.scalars().all()
    return ApiResponse.success(data=list(tasks))


@router.get(
    "/{spider_id}/tasks/{task_id}/logs",
    response_model=ApiResponse[List[TaskLogOut]],
    summary="获取指定任务的日志",
)
async def get_task_logs(
    spider_id: int = Path(...),
    task_id: str = Path(...),
    skip: int = Query(0),
    limit: int = Query(1000),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")

    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    result = await session.execute(
        select(TaskLog)
        .where(TaskLog.task_id == task_id)
        .order_by(TaskLog.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    logs = result.scalars().all()
    return ApiResponse.success(data=list(logs))


@router.get("/{spider_id}/status", response_model=ApiResponse, summary="获取爬虫最新任务状态")
async def get_spider_status(
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    """返回爬虫最近一次任务的状态，若无任务返回 idle。"""
    spider = await session.get(Spider, spider_id)
    if not spider or spider.is_deleted:
        raise HTTPException(status_code=404, detail="Spider not found")

    verify_resource_owner(spider.owner_id, operator, resource_name="爬虫")

    result = await session.execute(
        select(SpiderTask)
        .where(SpiderTask.spider_id == spider_id)
        .order_by(SpiderTask.created_at.desc())
        .limit(1)
    )
    latest_task = result.scalars().first()
    if not latest_task:
        return ApiResponse.success(data={"status": "idle", "task_id": None})

    return ApiResponse.success(data={
        "status": latest_task.status,
        "task_id": latest_task.task_id,
        "started_at": latest_task.started_at.isoformat() if latest_task.started_at else None,
        "finished_at": latest_task.finished_at.isoformat() if latest_task.finished_at else None,
    })
