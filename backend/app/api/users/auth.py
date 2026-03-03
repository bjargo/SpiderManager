"""
认证后端配置
- JWT Bearer Token 策略
- FastAPIUsers 全局实例
- 导出 current_active_user 依赖
"""
import uuid

from fastapi_users import FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)

from app.api.users.models import User
from app.api.users.manager import get_user_manager
from config import settings

# ---- Transport: 使用 Bearer 头传递 Token ----
bearer_transport = BearerTransport(tokenUrl="/api/users/login")


# ---- Strategy: JWT ----
def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.SECRET_KEY,
        lifetime_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# ---- Backend 组合 ----
auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

# ---- FastAPIUsers 全局实例 ----
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)

# ---- 常用依赖导出 ----
current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
