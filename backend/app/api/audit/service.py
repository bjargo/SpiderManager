import json
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request

from app.api.audit.models import AuditLog
from app.api.users.models import User

async def record_audit_log(
    session: AsyncSession,
    operator: User,
    action: str,
    resource_type: str,
    resource_id: str,
    status_code: int = 200,
    original_value: str | None = None,
    new_value: str | None = None,
    request: Request | None = None,
):
    """
    记录审计日志。
    日志将加入当前 session 的事务中，随外层 commit 一起生效。
    """
    ip_address = None
    if request and request.client:
        ip_address = request.client.host
    elif request and "x-forwarded-for" in request.headers:
        ip_address = request.headers["x-forwarded-for"].split(",")[0].strip()

    # 处理 role：适配 UserRole 枚举或直接字符串
    role_str = "viewer"
    if operator.role:
        role_str = operator.role.value if hasattr(operator.role, "value") else str(operator.role)

    # 确保存储字符串
    res_id_str = str(resource_id)

    log_entry = AuditLog(
        operator_id=operator.id,
        role=role_str,
        action=action.upper(),
        resource_type=resource_type.lower(),
        resource_id=res_id_str,
        original_value=original_value,
        new_value=new_value,
        ip_address=ip_address,
        status_code=status_code,
    )
    session.add(log_entry)
