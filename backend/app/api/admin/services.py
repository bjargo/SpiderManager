"""
管理员模块业务逻辑层

将审计日志查询的公共逻辑和用户管理逻辑从 router.py 分离出来。
消除 GET/POST 审计日志查询中的代码重复。
"""
import csv
import io
import uuid
import logging
import secrets
import string
from datetime import datetime as dt
from typing import Generator
from urllib.parse import quote

from fastapi import HTTPException, status, Request
from fastapi.responses import StreamingResponse
from fastapi_users import exceptions as fu_exc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.schemas import (
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminSetUserStatusRequest,
    AuditLogOut,
)
from app.core.audit.models import AuditLog
from app.api.users.models import User
from app.api.users.schemas import UserCreate
from app.api.users.manager import UserManager

logger = logging.getLogger(__name__)

# 密码字符集和最小长度
_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"
_PASSWORD_MIN_LEN = 14


def _generate_strong_password(length: int = _PASSWORD_MIN_LEN) -> str:
    """
    生成指定长度的强随机密码。

    确保至少包含大写字母、小写字母、数字和特殊字符各一个。

    :param length: 密码长度（默认 14 位）
    :return: 生成的强密码字符串
    """
    while True:
        pwd = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if (
            any(c.isupper() for c in pwd)
            and any(c.islower() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*" for c in pwd)
        ):
            return pwd


async def admin_create_user(
    body: AdminCreateUserRequest,
    operator: User,
    user_manager: UserManager,
    request: Request,
) -> AdminCreateUserResponse:
    """
    管理员创建新用户账号。

    系统强制生成 14 位随机初始密码并一次性返回给管理员。

    :param body: 用户创建请求体（email、role、is_verified）
    :param operator: 当前操作者（需 admin 角色）
    :param user_manager: FastAPI-Users 用户管理器
    :param request: FastAPI 请求对象
    :return: AdminCreateUserResponse 包含新用户 ID、email、角色和初始密码
    :raises HTTPException: 409 — 邮箱已注册；500 — 创建失败
    """
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

    return AdminCreateUserResponse(
        id=new_user.id,
        email=new_user.email,
        role=new_user.role,
        initial_password=initial_password,
    )


async def set_user_status(
    user_id: uuid.UUID,
    body: AdminSetUserStatusRequest,
    operator: User,
    session: AsyncSession,
) -> dict:
    """
    禁用或启用指定用户账号。

    禁止管理员对自身操作（防止自锁）。

    :param user_id: 目标用户 UUID
    :param body: 包含 is_active 字段的请求体
    :param operator: 当前操作者（需 admin 角色）
    :param session: 异步数据库会话
    :return: {"user_id": ..., "is_active": ...}
    :raises HTTPException: 400 — 自操作；404 — 用户不存在
    """
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

    return {
        "user_id": str(user_id),
        "is_active": body.is_active,
        "email": target_user.email,
        "action_desc": action_desc,
    }


# ── 审计日志查询公共逻辑 ──────────────────────────────────────────────


def _build_audit_query(
    operator_id: uuid.UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
):
    """
    构建审计日志查询语句（含 User.email 左连接）。

    此方法封装了 GET 和 POST 查询接口的公共查询构建逻辑，消除代码重复。

    :param operator_id: 操作者 UUID 筛选
    :param action: 操作动作筛选（自动转大写）
    :param resource_type: 资源类型筛选（自动转小写）
    :param start_time: 起始时间（ISO 8601 格式）
    :param end_time: 截止时间（ISO 8601 格式）
    :return: SQLAlchemy Select 语句
    :raises HTTPException: 400 — 时间格式错误
    """
    stmt = select(AuditLog, User.email).outerjoin(User, AuditLog.operator_id == User.id)

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

    return stmt


async def query_audit_logs(
    session: AsyncSession,
    operator_id: uuid.UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[AuditLogOut]:
    """
    执行审计日志查询，返回带操作者邮箱的结果列表。

    :param session: 异步数据库会话
    :param operator_id: 操作者 UUID 筛选
    :param action: 操作动作筛选
    :param resource_type: 资源类型筛选
    :param start_time: 起始时间
    :param end_time: 截止时间
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :return: AuditLogOut 列表
    """
    stmt = _build_audit_query(operator_id, action, resource_type, start_time, end_time)
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    result = await session.execute(stmt)
    rows = result.all()

    return _rows_to_audit_log_out(rows)


def _rows_to_audit_log_out(rows) -> list[AuditLogOut]:
    """
    将查询结果行转换为 AuditLogOut 列表。

    :param rows: SQLAlchemy 查询结果行（包含 AuditLog 和 email）
    :return: AuditLogOut 列表
    """
    data = []
    for log, email in rows:
        out = AuditLogOut.model_validate(log)
        out.operator_email = email or "已删除用户"
        data.append(out)
    return data


async def export_audit_logs_csv(
    session: AsyncSession,
    operator_id: uuid.UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> StreamingResponse:
    """
    导出审计日志为 CSV 文件下载。

    :param session: 异步数据库会话
    :param operator_id: 操作者 UUID 筛选
    :param action: 操作动作筛选
    :param resource_type: 资源类型筛选
    :param start_time: 起始时间
    :param end_time: 截止时间
    :return: StreamingResponse（CSV 文件流）
    """
    stmt = _build_audit_query(operator_id, action, resource_type, start_time, end_time)
    stmt = stmt.order_by(AuditLog.created_at.desc())

    async with session:
        result = await session.execute(stmt)
        rows = result.all()

        def iter_csv() -> Generator[str, None, None]:
            """
            逐行生成 CSV 内容的生成器。

            :return: 每次 yield 一行 CSV 文本
            """
            output = io.StringIO()
            output.write("\ufeff")  # UTF-8 BOM，解决 Excel 乱码
            writer = csv.writer(output)
            writer.writerow([
                "ID", "操作者 ID", "操作者邮箱", "角色", "动作", "资源类型",
                "资源 ID", "旧值", "新值", "IP 地址", "状态码", "时间",
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

            for log, email in rows:
                writer.writerow([
                    log.id,
                    str(log.operator_id),
                    email or "已删除用户",
                    log.role,
                    log.action,
                    log.resource_type,
                    log.resource_id,
                    log.original_value or "",
                    log.new_value or "",
                    log.ip_address or "",
                    log.status_code,
                    log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                ])
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

    filename = f"audit_logs_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = {
        "Content-Disposition": f"attachment; filename*=utf-8''{quote(filename)}",
    }
    return StreamingResponse(iter_csv(), media_type="text/csv", headers=headers)
