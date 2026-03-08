import logging
from typing import Optional
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.context import current_user_ctx, current_request_ctx
from app.api.users.auth import get_jwt_strategy
from app.api.users.manager import UserManager
from app.api.users.models import User
from app.db.database import async_session_maker
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

logger = logging.getLogger(__name__)

class AuditContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_token = current_request_ctx.set(request)
        user_token = None
        
        try:
            user = await self._get_user(request)
            user_token = current_user_ctx.set(user)
            
            response = await call_next(request)
            return response
        finally:
            current_request_ctx.reset(req_token)
            if user_token is not None:
                current_user_ctx.reset(user_token)

    async def _get_user(self, request: Request) -> Optional[User]:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
            
        token = auth_header.split(" ")[1]
        strategy = get_jwt_strategy()
        
        try:
            async with async_session_maker() as session:
                user_db = SQLAlchemyUserDatabase(session, User)
                user_manager = UserManager(user_db)
                user = await strategy.read_token(token, user_manager)
                return user
        except Exception as e:
            logger.warning(f"Failed to parse user in AuditContextMiddleware: {e}")
            return None
