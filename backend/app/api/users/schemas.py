"""
用户请求/响应 Schema
继承 fastapi-users 提供的 base schema。
"""
import uuid
from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    """用户信息响应（读取）"""
    pass


class UserCreate(schemas.BaseUserCreate):
    """用户注册请求"""
    pass


class UserUpdate(schemas.BaseUserUpdate):
    """用户信息更新请求"""
    pass
