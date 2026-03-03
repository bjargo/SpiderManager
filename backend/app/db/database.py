"""
数据库 Session 管理模块 (全异步)
使用 SQLAlchemy AsyncSession + asyncpg 驱动。
"""
import logging
from typing import AsyncGenerator

from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import create_engine as create_sync_engine

from config import settings

logger = logging.getLogger(__name__)

# ========== 异步引擎（所有业务模块统一使用）==========
async_engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
async_session_maker = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)

# ========== 同步引擎（仅用于 create_tables 启动时建表）==========
_sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
_sync_engine = create_sync_engine(_sync_url, echo=False)


def create_tables() -> None:
    """在应用启动时自动创建所有已注册的 SQLModel 表（如不存在）。"""
    import app.api.spiders.models  # noqa: F401
    import app.api.logs.models  # noqa: F401
    import app.api.messages.models  # noqa: F401
    import app.api.projects.models  # noqa: F401
    import app.api.users.models  # noqa: F401
    import app.api.tasks.models  # noqa: F401
    import app.api.tasks.task_log_models  # noqa: F401
    try:
        SQLModel.metadata.create_all(_sync_engine)
        logger.info("Database tables created/verified successfully.")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入：提供异步数据库 Session。
    用法: session: AsyncSession = Depends(get_async_session)
    """
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
