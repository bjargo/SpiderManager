"""
用户认证与管理路由
将 fastapi-users 提供的 auth / register / users 路由统一注册在此模块。
自定义 Admin 专属接口（列出所有用户、一键验证用户）也在此处定义。
"""
import uuid
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.users.auth import fastapi_users, auth_backend
from app.api.users.schemas import UserRead, UserCreate, UserUpdate
from app.api.users.models import User
from app.core.dependencies import require_admin
from app.db.database import get_async_session
from app.core.schemas.api_response import ApiResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# ── fastapi-users 内置路由 ──────────────────────────────────────────────────

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


# ── Admin 专属接口 ──────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=ApiResponse[List[UserRead]],
    tags=["用户管理"],
    summary="[Admin] 获取所有用户列表",
)
async def list_all_users(
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_admin),
) -> ApiResponse[List[UserRead]]:
    """返回系统内全部用户信息，仅 Admin 角色可访问。"""
    result = await session.execute(select(User))
    users = result.scalars().all()
    return ApiResponse.success(
        data=[UserRead.model_validate(u) for u in users]
    )


@router.post(
    "/{user_id}/verify",
    response_model=ApiResponse[UserRead],
    tags=["用户管理"],
    summary="[Admin] 手动验证指定用户",
)
async def verify_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_admin),
) -> ApiResponse[UserRead]:
    """
    将指定用户的 ``is_verified`` 设置为 ``True``。
    仅 Admin 角色可调用，用于免邮件验证场景（内网系统、手动审批等）。
    """
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"用户 {user_id} 不存在",
        )

    if user.is_verified:
        # 幂等：已验证则直接返回，不报错
        logger.info("User %s is already verified, skipping.", user_id)
        return ApiResponse.success(
            data=UserRead.model_validate(user),
            message="该用户已处于验证状态",
        )

    user.is_verified = True
    session.add(user)
    await session.commit()
    await session.refresh(user)

    logger.info(
        "Admin %s verified user %s (%s)",
        operator.email, user.id, user.email,
    )
    return ApiResponse.success(
        data=UserRead.model_validate(user),
        message=f"用户 {user.email} 已成功验证",
    )
