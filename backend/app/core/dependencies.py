"""
安全依赖项模块（FastAPI Depends）

提供三类可复用依赖：
1. get_current_verified_user  — 实时校验 is_active + is_verified，失败即 401
2. RoleChecker                — 声明式角色白名单检查
3. verify_resource_owner      — 非 Admin 用户的资源所有权校验辅助函数
"""
import uuid
import logging
from typing import List, Optional

from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.users.models import User
from app.api.users.auth import fastapi_users
from app.core.enums import UserRole
from app.db.database import get_async_session

logger = logging.getLogger(__name__)

# ── 基础依赖：fastapi-users 提供的原始已登录用户（不含 active 过滤）──
# 用 optional=False 确保未登录立即 401
_raw_current_user = fastapi_users.current_user(optional=False)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 实时状态校验依赖
# ─────────────────────────────────────────────────────────────────────────────

async def get_current_verified_user(
    user: User = Depends(_raw_current_user),
) -> User:
    """
    每次请求均检查 is_active 和 is_verified。
    若任意一项为 False，立即抛出 401，强制前端清除 Token/Cookie。

    用法：
        user: User = Depends(get_current_verified_user)
    """
    if not user.is_active:
        logger.warning("Blocked inactive user: id=%s email=%s", user.id, user.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号已被禁用，请联系管理员",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_verified:
        logger.warning("Blocked unverified user: id=%s email=%s", user.id, user.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号尚未完成邮箱验证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ─────────────────────────────────────────────────────────────────────────────
# 2. 角色鉴权依赖（RoleChecker）
# ─────────────────────────────────────────────────────────────────────────────

class RoleChecker:
    """
    声明式角色白名单校验依赖。

    用法：
        @router.post("/")
        async def create(
            user: User = Depends(RoleChecker([UserRole.admin, UserRole.developer]))
        ): ...

    is_superuser 用户绕过所有角色限制（向下兼容 fastapi-users 超管逻辑）。
    """

    def __init__(self, allowed_roles: List[UserRole]) -> None:
        self.allowed_roles = allowed_roles

    async def __call__(
        self,
        user: User = Depends(get_current_verified_user),
    ) -> User:
        # superuser 无视角色限制
        if user.is_superuser:
            return user

        if user.role not in self.allowed_roles:
            logger.warning(
                "Permission denied: user=%s role=%s required=%s",
                user.id, user.role, self.allowed_roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足，该操作要求角色：{[r.value for r in self.allowed_roles]}",
            )
        return user


# ── 常用预构建实例，直接 Depends 即可 ──
require_admin = RoleChecker([UserRole.admin])
require_developer = RoleChecker([UserRole.admin, UserRole.developer])
require_viewer = RoleChecker([UserRole.admin, UserRole.developer, UserRole.viewer])


# ─────────────────────────────────────────────────────────────────────────────
# 3. 资源所有权校验辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def verify_resource_owner(
    resource_owner_id: Optional[uuid.UUID],
    current_user: User,
    *,
    resource_name: str = "资源",
) -> None:
    """
    校验非 Admin 用户只能操作自己拥有的资源。

    规则：
    - is_superuser 或 role == admin → 跳过校验，直接通过
    - 其他角色 → resource.owner_id 必须等于 current_user.id

    参数：
        resource_owner_id: 资源的 owner_id 字段值（可为 None，视为无主资源，Admin 可操作）
        current_user:      当前已鉴权用户
        resource_name:     用于错误提示的资源类型名称

    用法（在路由函数内部调用）：
        verify_resource_owner(spider.owner_id, current_user, resource_name="爬虫")

    Raises:
        HTTPException 403 — 当非 Admin 用户尝试操作他人资源
    """
    if current_user.is_superuser or current_user.role == UserRole.admin:
        return  # Admin 无限制

    if resource_owner_id != current_user.id:
        logger.warning(
            "Ownership check failed: user=%s tried to access %s owned by %s",
            current_user.id, resource_name, resource_owner_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"无权操作他人的{resource_name}",
        )
