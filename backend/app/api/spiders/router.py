"""
爬虫管理路由 — 纯接口层

所有业务逻辑已迁移到 services.py，本模块仅负责：
1. 路由注册与请求参数解析
2. 调用 service 层完成业务操作
3. 统一响应格式（ApiResponse）
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, Query, Path, UploadFile, File, BackgroundTasks
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.spiders.schemas import (
    SpiderCreate, SpiderUpdate, SpiderOut, SpiderRunRequest, SpiderFileSave,
    SpiderFileCreate, SpiderFileDelete,
    SpiderTaskOut, TaskLogOut,
)
from app.api.users.models import User
from app.db.database import get_async_session
from app.core.redis import get_redis
from app.core.schemas.api_response import ApiResponse
from app.core.dependencies import require_viewer, require_developer
from app.core.audit.service import audit_log
from app.api.spiders import services

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/upload", response_model=ApiResponse, summary="上传爬虫代码 ZIP 包")
async def upload_spider_zip(file: UploadFile = File(...)):
    """
    接收前端上传的 .zip 文件并存储到 MinIO。

    :param file: 上传的 ZIP 文件
    :return: ApiResponse 包含 source_url
    """
    source_url = await services.upload_zip(file)
    return ApiResponse.success(data={"source_url": source_url})


@router.post("", response_model=ApiResponse[SpiderOut], summary="创建爬虫")
@audit_log(action="CREATE", resource_type="spider")
async def create_spider(
    spider_in: SpiderCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    创建新爬虫记录并持久化到数据库。

    :param spider_in: 爬虫创建请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含创建后的爬虫信息
    """
    spider = await services.create_spider(spider_in, operator, session)
    return ApiResponse.success(data=spider)


@router.get("", response_model=ApiResponse[List[SpiderOut]], summary="获取爬虫列表")
async def read_spiders(
    skip: int = Query(0),
    limit: int = Query(100),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    """
    分页查询未删除的爬虫列表。

    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含爬虫列表
    """
    spiders = await services.get_spider_list(skip, limit, session)
    return ApiResponse.success(data=spiders)


@router.get("/{spider_id}", response_model=ApiResponse[SpiderOut], summary="获取指定爬虫")
async def read_spider(
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    """
    根据 ID 获取单个爬虫详情。

    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含爬虫详情
    """
    spider = await services.get_spider_by_id(spider_id, session)
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
    """
    更新指定爬虫的配置信息。

    :param spider_in: 爬虫更新请求体（仅包含需更新的字段）
    :param background_tasks: 后台任务（审计日志使用）
    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含更新后的爬虫信息
    """
    spider = await services.update_spider(spider_id, spider_in, operator, session)
    return ApiResponse.success(data=spider)


@router.post("/{spider_id}/delete", response_model=ApiResponse, summary="删除爬虫")
@audit_log(action="DELETE", resource_type="spider")
async def delete_spider(
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    软删除指定爬虫（标记 is_deleted = True）。

    :param background_tasks: 后台任务（审计日志使用）
    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 成功消息
    """
    await services.delete_spider(spider_id, operator, session)
    return ApiResponse.success(message="Spider deleted successfully")


@router.get("/{spider_id}/files", response_model=ApiResponse, summary="列出爬虫 ZIP 包内的文件列表")
async def list_spider_files(
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
):
    """
    列出爬虫 ZIP 代码包中的文件树。

    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :return: ApiResponse 包含文件路径列表
    """
    files = await services.list_spider_files(spider_id, session)
    return ApiResponse.success(data=files)


@router.get("/{spider_id}/file", response_model=ApiResponse, summary="读取爬虫 ZIP 包内指定文件内容")
async def read_spider_file(
    spider_id: int = Path(...),
    path: str = Query(..., description="ZIP 包内的文件路径"),
    session: AsyncSession = Depends(get_async_session),
):
    """
    读取爬虫 ZIP 代码包中指定文件的文本内容。

    :param spider_id: 爬虫主键 ID
    :param path: ZIP 包内的文件路径
    :param session: 注入的数据库会话
    :return: ApiResponse 包含 {"path": ..., "content": ...}
    """
    data = await services.read_spider_file(spider_id, path, session)
    return ApiResponse.success(data=data)


@router.post("/{spider_id}/file", response_model=ApiResponse, summary="保存修改后的文件到爬虫 ZIP 包")
@audit_log(action="UPDATE", resource_type="spider")
async def save_spider_file(
    body: SpiderFileSave,
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    替换爬虫 ZIP 包内指定文件的内容。

    :param body: 包含 path 和 content 的请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 成功消息
    """
    await services.save_spider_file(spider_id, body, operator, session)
    return ApiResponse.success(message="文件保存成功")


@router.post("/{spider_id}/file/create", response_model=ApiResponse, summary="在爬虫 ZIP 包内新增文件")
@audit_log(action="CREATE", resource_type="spider_file")
async def create_spider_file(
    body: SpiderFileCreate,
    background_tasks: BackgroundTasks,
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    在爬虫 ZIP 代码包中创建新文件。

    :param body: 包含 path 和 content 的请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含更新后的文件列表
    """
    files = await services.create_spider_file(spider_id, body, operator, session)
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
    """
    从爬虫 ZIP 代码包中删除指定文件。

    :param body: 包含 path 的请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含更新后的文件列表
    """
    files = await services.delete_spider_file(spider_id, body, operator, session)
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
    将爬虫运行任务推送到 Redis 队列。

    :param body: 运行请求体（包含 target_nodes、timeout_seconds）
    :param background_tasks: 后台任务（审计日志使用）
    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :param redis: 注入的 Redis 客户端
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含 task_id
    """
    task_id = await services.run_spider(spider_id, body, operator, session, redis)
    return ApiResponse.success(data={"task_id": task_id}, message="任务已进入调度队列")


@router.get("/{spider_id}/tasks", response_model=ApiResponse[List[SpiderTaskOut]], summary="获取爬虫的任务历史")
async def list_spider_tasks(
    spider_id: int = Path(...),
    skip: int = Query(0),
    limit: int = Query(50),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    """
    分页查询指定爬虫的任务执行历史。

    :param spider_id: 爬虫主键 ID
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含任务列表
    """
    tasks = await services.list_spider_tasks(spider_id, skip, limit, operator, session)
    return ApiResponse.success(data=tasks)


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
    """
    查询指定爬虫任务的日志记录。

    :param spider_id: 爬虫主键 ID
    :param task_id: 任务 UUID
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含日志列表
    """
    logs = await services.get_spider_task_logs(spider_id, task_id, skip, limit, operator, session)
    return ApiResponse.success(data=logs)


@router.get("/{spider_id}/status", response_model=ApiResponse, summary="获取爬虫最新任务状态")
async def get_spider_status(
    spider_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    """
    返回爬虫最近一次任务的状态，若无任务返回 idle。

    :param spider_id: 爬虫主键 ID
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含 status、task_id 等字段
    """
    data = await services.get_spider_status(spider_id, operator, session)
    return ApiResponse.success(data=data)
