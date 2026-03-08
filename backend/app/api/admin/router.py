"""
管理员路由模块

所有接口均要求 admin 角色，通过 require_admin 依赖统一鉴权。

接口列表：
  POST /admin/users               — 管理员创建用户（系统生成随机密码）
  POST /admin/users/{id}/status   — 禁用/启用用户
  GET  /admin/logs                — 审计日志分页筛选查询
"""
import uuid
import logging
import secrets
import string
from typing import List

import sys
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_users import exceptions as fu_exc

from app.api.admin.schemas import (
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminSetUserStatusRequest,
    AdminLogQueryRequest,
    AuditLogOut,
)
from app.core.audit.models import AuditLog
from app.api.users.models import User
from app.api.users.schemas import UserCreate
from app.api.users.manager import get_user_manager, UserManager
from app.core.dependencies import require_admin, get_current_verified_user
from app.core.schemas.api_response import ApiResponse
from app.db.database import get_async_session

router = APIRouter()
logger = logging.getLogger(__name__)

# 密码字符集：大小写字母 + 数字 + 特殊字符，确保强度
_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"
_PASSWORD_MIN_LEN = 14


def _generate_strong_password(length: int = _PASSWORD_MIN_LEN) -> str:
    """生成指定长度的强随机密码（至少包含大写、小写、数字、特殊字符各一个）。"""
    while True:
        pwd = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if (
            any(c.isupper() for c in pwd)
            and any(c.islower() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*" for c in pwd)
        ):
            return pwd


# ─────────────────────────────────────────────────────────────────────────────
# POST /admin/users — 管理员创建用户
# ─────────────────────────────────────────────────────────────────────────────

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
    initial_password = _generate_strong_password()

    user_create = UserCreate(
        email=body.email,
        password=initial_password,
        role=body.role,
        is_verified=body.is_verified,
    )

    try:
        new_user = await user_manager.create(user_create, safe=False, request=request)
    except fu_exc.UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"邮箱 {body.email} 已被注册",
        )
    except Exception as exc:
        logger.error("admin_create_user error: %s", exc)
        raise HTTPException(status_code=500, detail="创建用户失败，请查看服务日志")

    logger.info(
        "Admin %s created user %s (email=%s role=%s)",
        operator.id, new_user.id, new_user.email, body.role,
    )

    return ApiResponse.success(
        data=AdminCreateUserResponse(
            id=new_user.id,
            email=new_user.email,
            role=new_user.role,
            initial_password=initial_password,
        ),
        message="用户创建成功，请将初始密码安全地告知用户并要求立即修改",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /admin/users/{user_id}/status — 禁用/启用用户
# ─────────────────────────────────────────────────────────────────────────────

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
    # 禁止管理员对自身操作（防止自锁）
    if user_id == operator.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能对自己的账号执行禁用/启用操作",
        )

    target_user = await session.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    old_status = target_user.is_active
    target_user.is_active = body.is_active
    session.add(target_user)
    await session.commit()

    action_desc = "启用" if body.is_active else "禁用"
    logger.info(
        "Admin %s %s user %s (email=%s): is_active %s -> %s",
        operator.id, action_desc, user_id, target_user.email, old_status, body.is_active,
    )

    return ApiResponse.success(
        message=f"用户 {target_user.email} 已{action_desc}",
        data={"user_id": str(user_id), "is_active": body.is_active},
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /admin/logs — 审计日志筛选查询
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/logs",
    response_model=ApiResponse[List[AuditLogOut]],
    summary="审计日志查询",
    description=(
        "管理员专属接口，支持按操作者、操作动作、资源类型、时间范围筛选审计日志。"
        "所有参数均为可选，不传则返回最新 N 条。"
    ),
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
    GET 版本：通过 Query 参数传递筛选条件（便于前端直接拼 URL）。
    """
    from datetime import datetime as dt

    stmt = select(AuditLog)

    if operator_id is not None:
        stmt = stmt.where(AuditLog.operator_id == operator_id)
    if action:
        stmt = stmt.where(AuditLog.action == action.upper())
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type.lower())
    if start_time:
        try:
            stmt = stmt.where(AuditLog.created_at >= dt.fromisoformat(start_time))
        except ValueError:
            raise HTTPException(status_code=400, detail="start_time 格式错误，请使用 ISO 8601 格式")
    if end_time:
        try:
            stmt = stmt.where(AuditLog.created_at <= dt.fromisoformat(end_time))
        except ValueError:
            raise HTTPException(status_code=400, detail="end_time 格式错误，请使用 ISO 8601 格式")

    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    result = await session.execute(stmt)
    logs = result.scalars().all()

    return ApiResponse.success(data=[AuditLogOut.model_validate(log) for log in logs])


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
    stmt = select(AuditLog)

    if body.operator_id is not None:
        stmt = stmt.where(AuditLog.operator_id == body.operator_id)
    if body.action:
        stmt = stmt.where(AuditLog.action == body.action.upper())
    if body.resource_type:
        stmt = stmt.where(AuditLog.resource_type == body.resource_type.lower())
    if body.start_time:
        stmt = stmt.where(AuditLog.created_at >= body.start_time)
    if body.end_time:
        stmt = stmt.where(AuditLog.created_at <= body.end_time)

    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(body.skip).limit(body.limit)
    result = await session.execute(stmt)
    logs = result.scalars().all()

    return ApiResponse.success(data=[AuditLogOut.model_validate(log) for log in logs])


# ─────────────────────────────────────────────────────────────────────────────
# GET /admin/logs/export — 审计日志导出 CSV
# ─────────────────────────────────────────────────────────────────────────────

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
    from datetime import datetime as dt
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())

    if operator_id is not None:
        stmt = stmt.where(AuditLog.operator_id == operator_id)
    if action:
        stmt = stmt.where(AuditLog.action == action.upper())
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type.lower())
    if start_time:
        try:
            stmt = stmt.where(AuditLog.created_at >= dt.fromisoformat(start_time))
        except ValueError:
            raise HTTPException(status_code=400, detail="start_time 格式错误")
    if end_time:
        try:
            stmt = stmt.where(AuditLog.created_at <= dt.fromisoformat(end_time))
        except ValueError:
            raise HTTPException(status_code=400, detail="end_time 格式错误")

    result = await session.execute(stmt)
    logs = result.scalars().all()

    def iter_csv():
        output = io.StringIO()
        # 处理 Windows Excel 中文乱码（UTF-8 BOM）
        output.write('\ufeff')
        writer = csv.writer(output)
        writer.writerow([
            "ID", "操作者 ID", "角色", "动作", "资源类型",
            "资源 ID", "旧值", "新值", "IP 地址", "状态码", "时间"
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for log in logs:
            writer.writerow([
                log.id,
                str(log.operator_id),
                log.role,
                log.action,
                log.resource_type,
                log.resource_id,
                log.original_value or "",
                log.new_value or "",
                log.ip_address or "",
                log.status_code,
                log.created_at.strftime("%Y-%m-%d %H:%M:%S")
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    from urllib.parse import quote
    filename = f"audit_logs_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = {
        "Content-Disposition": f"attachment; filename*=utf-8''{quote(filename)}"
    }
    return StreamingResponse(iter_csv(), media_type="text/csv", headers=headers)
