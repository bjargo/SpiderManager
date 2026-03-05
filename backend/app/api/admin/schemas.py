"""
管理员模块 Schema 定义
"""
import uuid
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

from app.common.enums import UserRole


# ── 请求体 ──────────────────────────────────────────────────────────────────

class AdminCreateUserRequest(BaseModel):
    """管理员创建用户：只需指定 email 和角色，密码由系统强制生成。"""
    email: EmailStr
    role: UserRole = UserRole.developer
    is_verified: bool = True          # 管理员创建的用户默认已验证


class AdminSetUserStatusRequest(BaseModel):
    """管理员禁用/启用用户请求体。"""
    is_active: bool = Field(description="True=启用  False=禁用")


class AdminLogQueryRequest(BaseModel):
    """审计日志筛选查询参数（POST body）。"""
    operator_id: Optional[uuid.UUID] = Field(default=None, description="操作者 UUID 精确匹配")
    action: Optional[str] = Field(default=None, max_length=64, description="操作动作（如 CREATE/DELETE）")
    resource_type: Optional[str] = Field(default=None, max_length=64, description="资源类型（spider/project/user）")
    start_time: Optional[datetime] = Field(default=None, description="起始时间（含）")
    end_time: Optional[datetime] = Field(default=None, description="截止时间（含）")
    skip: int = Field(default=0, ge=0, description="分页偏移")
    limit: int = Field(default=50, ge=1, le=500, description="每页最大条数")


# ── 响应体 ──────────────────────────────────────────────────────────────────

class AdminCreateUserResponse(BaseModel):
    """创建用户的响应，包含明文初始密码（仅此一次）。"""
    id: uuid.UUID
    email: str
    role: UserRole
    initial_password: str = Field(description="系统生成的初始明文密码，仅此一次展示，请立即告知用户并要求修改")


class AuditLogOut(BaseModel):
    """审计日志单条输出 Schema。"""
    id: int
    operator_id: uuid.UUID
    role: str
    action: str
    resource_type: str
    resource_id: str
    original_value: Optional[str]
    new_value: Optional[str]
    ip_address: Optional[str]
    status_code: int
    created_at: datetime

    model_config = {"from_attributes": True}
