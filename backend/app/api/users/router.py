"""
用户认证与管理路由
将 fastapi-users 提供的 auth / register / users 路由统一注册在此模块。
"""
from fastapi import APIRouter

from app.api.users.auth import fastapi_users, auth_backend
from app.api.users.schemas import UserRead, UserCreate, UserUpdate

router = APIRouter()

# 登录 / 登出
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    tags=["用户认证"],
)

# 注册
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    tags=["用户认证"],
)

# 用户管理 (me / {id})
router.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    tags=["用户管理"],
)
