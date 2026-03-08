import logging
from sqlmodel import select
from app.api.users.models import User
from app.core.enums import UserRole
from app.db.database import async_session_maker
from fastapi_users.password import PasswordHelper

logger = logging.getLogger(__name__)

async def init_superuser(email: str, password: str) -> None:
    """初始化默认超级管理员"""
    try:
        async with async_session_maker() as session:
            stmt = select(User).where(User.email == email)
            result = await session.execute(stmt)
            user = result.scalars().first()

            if not user:
                password_helper = PasswordHelper()
                hashed_password = password_helper.hash(password)
                new_user = User(
                    email=email,
                    hashed_password=hashed_password,
                    is_active=True,
                    is_superuser=True,
                    is_verified=True,
                    role=UserRole.admin
                )
                session.add(new_user)
                await session.commit()
                logger.info(f"Initial superuser created: {email}")
            else:
                logger.info(f"Superuser already exists: {email}")
    except Exception as e:
        logger.error(f"Failed to create initial superuser: {e}")
