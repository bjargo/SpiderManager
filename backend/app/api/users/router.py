"""
用户认证与管理路由（仅 GET / POST）

设计约束：对外接口只允许 **GET** 与 **POST** 两种方法，
因此不使用 fastapi-users 的 ``get_users_router``（其内部注册了
``PATCH /me``、``PATCH /{id}``、``DELETE /{id}``），改为在本模块手写等价端点：

  GET  /users/me              — 当前登录用户信息
  POST /users/me/update       — 更新当前登录用户信息（替代 PATCH /me）
  GET  /users/{user_id}       — [Admin] 查询指定用户
  POST /users/{user_id}/update— [Admin] 更新指定用户（替代 PATCH /{id}）
  POST /users/{user_id}/delete— [Admin] 删除指定用户（替代 DELETE /{id}）

``get_auth_router`` / ``get_register_router`` 仅注册 POST 端点，
故仍直接 include。
"""
import uuid
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users import exceptions as fapi_exceptions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.users.auth import fastapi_users, auth_backend, current_active_user
from app.api.users.schemas import UserRead, UserCreate, UserSelfUpdate, UserUpdate
from app.api.users.models import User
from app.api.users.manager import get_user_manager, UserManager
from app.core.dependencies import require_admin
from app.db.database import get_async_session
from app.core.schemas.api_response import ApiResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# ── fastapi-users 内置路由（仅 POST）────────────────────────────────────────

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


# ── 当前用户（自助）──────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=ApiResponse[UserRead],
    tags=["用户管理"],
    summary="获取当前登录用户信息",
)
async def get_current_user_info(
    user: User = Depends(current_active_user),
) -> ApiResponse[UserRead]:
    """返回当前已登录用户的信息（含 role）。"""
    return ApiResponse.success(data=UserRead.model_validate(user))


@router.post(
    "/me/update",
    response_model=ApiResponse[UserRead],
    tags=["用户管理"],
    summary="更新当前登录用户信息",
    description="等价于 fastapi-users 的 PATCH /users/me，仅允许修改邮箱与密码。",
)
async def update_current_user_info(
    body: UserSelfUpdate,
    request: Request,
    user: User = Depends(current_active_user),
    user_manager: UserManager = Depends(get_user_manager),
) -> ApiResponse[UserRead]:
    """
    更新当前登录用户的邮箱 / 密码。

    请求体只接受 ``email`` 与 ``password``，不含 ``role`` 等权限字段，
    并通过 ``safe=True`` 交由 UserManager 过滤敏感字段，防止越权提权。
    """
    try:
        updated = await user_manager.update(body, user, safe=True, request=request)
    except fapi_exceptions.InvalidPasswordException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "UPDATE_USER_INVALID_PASSWORD", "reason": e.reason},
        )
    except fapi_exceptions.UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="UPDATE_USER_EMAIL_ALREADY_EXISTS",
        )

    logger.info("User %s updated own profile.", user.id)
    return ApiResponse.success(
        data=UserRead.model_validate(updated),
        message="用户信息已更新",
    )


# ── Admin 专属接口 ──────────────────────────────────────────────────────────

async def _get_user_or_404(user_id: uuid.UUID, session: AsyncSession) -> User:
    """按主键查询用户，不存在时返回 404。"""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"用户 {user_id} 不存在",
        )
    return user


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


@router.get(
    "/{user_id}",
    response_model=ApiResponse[UserRead],
    tags=["用户管理"],
    summary="[Admin] 查询指定用户",
)
async def get_user_by_id(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_admin),
) -> ApiResponse[UserRead]:
    """按 UUID 查询单个用户，仅 Admin 角色可访问。"""
    user = await _get_user_or_404(user_id, session)
    return ApiResponse.success(data=UserRead.model_validate(user))


@router.post(
    "/{user_id}/update",
    response_model=ApiResponse[UserRead],
    tags=["用户管理"],
    summary="[Admin] 更新指定用户",
    description="等价于 fastapi-users 的 PATCH /users/{id}，可修改邮箱、密码、角色与启用状态。",
)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    operator: User = Depends(require_admin),
    user_manager: UserManager = Depends(get_user_manager),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[UserRead]:
    """管理员更新指定用户信息（含 role / is_active / is_verified）。"""
    user = await _get_user_or_404(user_id, session)

    try:
        updated = await user_manager.update(body, user, safe=False, request=request)
    except fapi_exceptions.InvalidPasswordException as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "UPDATE_USER_INVALID_PASSWORD", "reason": e.reason},
        )
    except fapi_exceptions.UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="UPDATE_USER_EMAIL_ALREADY_EXISTS",
        )

    logger.info("Admin %s updated user %s", operator.email, updated.id)
    return ApiResponse.success(
        data=UserRead.model_validate(updated),
        message=f"用户 {updated.email} 已更新",
    )


@router.post(
    "/{user_id}/delete",
    response_model=ApiResponse,
    tags=["用户管理"],
    summary="[Admin] 删除指定用户",
    description="等价于 fastapi-users 的 DELETE /users/{id}。",
)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    operator: User = Depends(require_admin),
    user_manager: UserManager = Depends(get_user_manager),
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse:
    """管理员删除指定用户。"""
    user = await _get_user_or_404(user_id, session)

    if user.id == operator.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除当前登录的管理员账号",
        )

    await user_manager.delete(user, request=request)
    logger.info("Admin %s deleted user %s", operator.email, user_id)
    return ApiResponse.success(message=f"用户 {user_id} 已删除")


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
    user = await _get_user_or_404(user_id, session)

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
