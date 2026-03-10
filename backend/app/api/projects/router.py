"""
项目管理路由 — 纯接口层

所有业务逻辑已迁移到 services.py，本模块仅负责路由注册和请求响应。
"""
import logging
from typing import List

from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import audit_log
from app.api.projects.schemas import ProjectCreate, ProjectUpdate, ProjectOut
from app.api.users.models import User
from app.core.schemas.api_response import ApiResponse
from app.db.database import get_async_session
from app.core.dependencies import require_developer, require_viewer
from app.api.projects import services

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=ApiResponse[List[ProjectOut]], summary="获取所有项目")
async def list_projects(
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    """
    获取所有未删除的项目列表（含关联爬虫数量）。

    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含项目列表
    """
    result = await services.list_all(session)
    return ApiResponse.success(data=result)


@router.post("", response_model=ApiResponse[ProjectOut], summary="创建项目")
@audit_log(action="CREATE", resource_type="project")
async def create_project(
    body: ProjectCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    创建一个新的项目记录。

    :param body: 项目创建请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含创建后的项目信息
    """
    out = await services.create_project(body, operator, session)
    return ApiResponse.success(data=out)


@router.post("/{project_id}/update", response_model=ApiResponse[ProjectOut], summary="修改项目")
@audit_log(action="UPDATE", resource_type="project")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    更新指定项目的名称或描述。

    :param project_id: 项目 ID（proj-xxx 格式）
    :param body: 项目更新请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含更新后的项目信息
    """
    out = await services.update_project(project_id, body, operator, session)
    return ApiResponse.success(data=out)


@router.post("/{project_id}/delete", response_model=ApiResponse, summary="删除项目")
@audit_log(action="DELETE", resource_type="project")
async def delete_project(
    project_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_developer),
):
    """
    软删除项目及其关联的所有爬虫。

    :param project_id: 项目 ID（proj-xxx 格式）
    :param background_tasks: 后台任务（审计日志使用）
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: ApiResponse 成功消息
    """
    message = await services.delete_project(project_id, operator, session)
    return ApiResponse.success(message=message)
