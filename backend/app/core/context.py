import contextvars
from typing import Optional
from fastapi import Request
from app.api.users.models import User

current_user_ctx: contextvars.ContextVar[Optional[User]] = contextvars.ContextVar("current_user", default=None)
current_request_ctx: contextvars.ContextVar[Optional[Request]] = contextvars.ContextVar("current_request", default=None)
