"""
用户管理器（UserManager）
处理用户生命周期回调（注册后、登录后等）。
"""
import uuid
import logging
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users_db_sqlmodel import SQLModelUserDatabaseAsync

from app.api.users.models import User
from app.db.database import get_async_session

logger = logging.getLogger(__name__)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """自定义用户管理器，可重写回调以实现自定义逻辑。"""

    reset_password_token_secret = "RESET_SECRET"  # 可通过 settings 注入
    verification_token_secret = "VERIFY_SECRET"

    async def on_after_register(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        logger.info(f"User {user.email} (id={user.id}) has registered.")

    async def on_after_login(
        self, user: User, request: Optional[Request] = None, response=None
    ) -> None:
        logger.info(f"User {user.email} logged in.")


async def get_user_db(session=Depends(get_async_session)):
    """提供 fastapi-users 所需的用户数据库适配器。"""
    yield SQLModelUserDatabaseAsync(session, User)


async def get_user_manager(
    user_db: SQLModelUserDatabaseAsync = Depends(get_user_db),
):
    """提供 UserManager 实例。"""
    yield UserManager(user_db)
