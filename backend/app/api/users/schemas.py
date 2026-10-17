"""
用户请求/响应 Schema
继承 fastapi-users 提供的 base schema，并扩展 role 字段。
"""
import uuid
from typing import Optional
from fastapi_users import schemas

from app.core.enums import UserRole


class UserRead(schemas.BaseUser[uuid.UUID]):
    """用户信息响应（读取）"""
    role: UserRole


class UserCreate(schemas.BaseUserCreate):
    """用户注册请求"""
    role: UserRole = UserRole.developer


class UserUpdate(schemas.BaseUserUpdate):
    """用户信息更新请求（Admin 使用，可改 role）"""
    role: Optional[UserRole] = None


class UserSelfUpdate(schemas.BaseUserUpdate):
    """用户自助更新请求：不含 role，防止普通用户越权提升自身角色。"""
