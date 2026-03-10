"""
管理员路由 — 纯接口层

所有业务逻辑已迁移到 services.py，本模块仅负责路由注册和请求响应。

接口列表：
  POST /admin/users               — 管理员创建用户
  POST /admin/users/{id}/status   — 禁用/启用用户
  GET  /admin/logs                — 审计日志分页筛选查询
  POST /admin/logs/query          — 审计日志高级筛选（POST Body）
  GET  /admin/logs/export         — 审计日志导出 CSV
"""
import uuid
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.schemas import (
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminSetUserStatusRequest,
    AdminLogQueryRequest,
    AuditLogOut,
)
from app.api.users.models import User
from app.api.users.manager import get_user_manager, UserManager
from app.core.dependencies import require_admin
from app.core.schemas.api_response import ApiResponse
from app.db.database import get_async_session
from app.api.admin import services

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/users",
    response_model=ApiResponse[AdminCreateUserResponse],
    summary="管理员创建用户",
    description="由管理员创建新用户账号，系统强制生成 14 位随机初始密码并一次性返回给管理员。",
)
async def admin_create_user(
    body: AdminCreateUserRequest,
    request: Request,
    operator: User = Depends(require_admin),
    user_manager: UserManager = Depends(get_user_manager),
) -> ApiResponse[AdminCreateUserResponse]:
    """
    管理员创建新用户账号。

    :param body: 用户创建请求体
    :param request: FastAPI 请求对象
    :param operator: 注入的当前操作者（需 admin 角色）
    :param user_manager: 注入的用户管理器
    :return: ApiResponse 包含新用户信息和初始密码
    """
    data = await services.admin_create_user(body, operator, user_manager, request)
    return ApiResponse.success(
        data=data,
        message="用户创建成功，请将初始密码安全地告知用户并要求立即修改",
    )


@router.post(
    "/users/{user_id}/status",
    response_model=ApiResponse,
    summary="禁用/启用用户",
    description="管理员即时禁用或重新启用指定用户账号，效果对下一次请求立即生效。",
)
async def admin_set_user_status(
    user_id: uuid.UUID,
    body: AdminSetUserStatusRequest,
    request: Request,
    operator: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse:
    """
    禁用或启用指定用户账号。

    :param user_id: 目标用户 UUID
    :param body: 包含 is_active 的请求体
    :param request: FastAPI 请求对象
    :param operator: 注入的当前操作者（需 admin 角色）
    :param session: 注入的数据库会话
    :return: ApiResponse 包含操作结果
    """
    result = await services.set_user_status(user_id, body, operator, session)
    return ApiResponse.success(
        message=f"用户 {result['email']} 已{result['action_desc']}",
        data={"user_id": result["user_id"], "is_active": result["is_active"]},
    )


@router.get(
    "/logs",
    response_model=ApiResponse[List[AuditLogOut]],
    summary="审计日志查询",
    description="管理员专属接口，支持按操作者、操作动作、资源类型、时间范围筛选审计日志。",
)
async def admin_get_logs(
    operator_id: uuid.UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    skip: int = 0,
    limit: int = 50,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[List[AuditLogOut]]:
    """
    通过 Query 参数按条件筛选审计日志。

    :param operator_id: 操作者 UUID 筛选
    :param action: 操作动作筛选
    :param resource_type: 资源类型筛选
    :param start_time: 起始时间（ISO 8601）
    :param end_time: 截止时间（ISO 8601）
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param _: 注入的当前操作者（需 admin 角色）
    :param session: 注入的数据库会话
    :return: ApiResponse 包含审计日志列表
    """
    data = await services.query_audit_logs(
        session, operator_id, action, resource_type, start_time, end_time, skip, limit,
    )
    return ApiResponse.success(data=data)


@router.post(
    "/logs/query",
    response_model=ApiResponse[List[AuditLogOut]],
    summary="审计日志高级筛选（POST Body）",
    description="与 GET /admin/logs 等价，但通过 JSON Body 传参，适合复杂筛选场景。",
)
async def admin_query_logs(
    body: AdminLogQueryRequest,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[List[AuditLogOut]]:
    """
    通过 JSON Body 按条件筛选审计日志。

    :param body: 审计日志查询请求体
    :param _: 注入的当前操作者（需 admin 角色）
    :param session: 注入的数据库会话
    :return: ApiResponse 包含审计日志列表
    """
    # 将 body 中的 datetime 字段转为 ISO 字符串以复用公共逻辑
    start_time_str = body.start_time.isoformat() if body.start_time else None
    end_time_str = body.end_time.isoformat() if body.end_time else None

    data = await services.query_audit_logs(
        session, body.operator_id, body.action, body.resource_type,
        start_time_str, end_time_str, body.skip, body.limit,
    )
    return ApiResponse.success(data=data)


@router.get(
    "/logs/export",
    summary="导出审计日志为 CSV",
    description="支持与 GET /admin/logs 相同的筛选条件，并将结果导出为 CSV 文件下载。",
)
async def admin_export_logs(
    operator_id: uuid.UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_async_session),
):
    """
    导出审计日志为 CSV 文件下载。

    :param operator_id: 操作者 UUID 筛选
    :param action: 操作动作筛选
    :param resource_type: 资源类型筛选
    :param start_time: 起始时间（ISO 8601）
    :param end_time: 截止时间（ISO 8601）
    :param _: 注入的当前操作者（需 admin 角色）
    :param session: 注入的数据库会话
    :return: StreamingResponse（CSV 文件流）
    """
    return await services.export_audit_logs_csv(
        session, operator_id, action, resource_type, start_time, end_time,
    )
