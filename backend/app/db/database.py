"""
数据库 Session 管理模块 (全异步)
使用 SQLAlchemy AsyncSession + asyncpg 驱动。
"""
import logging
from typing import AsyncGenerator
from sqlalchemy import text
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

# ========== 爬虫数据专属引擎 ==========
spider_async_engine = create_async_engine(settings.SPIDER_DATABASE_URL, echo=False, pool_pre_ping=True)

# 运行配置相关列。create_all 只建表、不会给已存在的表补列，因此启动时再做一次幂等补齐；
# 全新部署时这些语句本身就是空操作，与原有部署流程一致，无需任何人工步骤。
_RUN_CONFIG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("spiders", "entrypoint"),
    ("spiders", "args"),
    ("spider_tasks", "entrypoint"),
    ("spider_tasks", "args"),
)


def _ensure_run_config_columns() -> None:
    """幂等补齐结构化运行配置列（已存在则跳过）。"""
    with _sync_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for table_name, column_name in _RUN_CONFIG_COLUMNS:
            conn.execute(
                text(
                    f'ALTER TABLE "{table_name}" '
                    f"ADD COLUMN IF NOT EXISTS {column_name} VARCHAR"
                )
            )


def create_tables() -> None:
    """在应用启动时自动创建所有已注册的 SQLModel 表（如不存在）。"""
    import app.api.spiders.models  # noqa: F401
    import app.api.logs.models  # noqa: F401
    import app.api.messages.models  # noqa: F401
    import app.api.projects.models  # noqa: F401
    import app.api.users.models  # noqa: F401
    import app.api.tasks.models  # noqa: F401
    import app.api.reports.models  # noqa: F401
    import app.core.audit.models  # noqa: F401
    try:
        with _sync_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            res = conn.execute(text(f"SELECT 1 FROM pg_database WHERE datname='{settings.SPIDER_DB_NAME}'"))
            if not res.fetchone():
                conn.execute(text(f'CREATE DATABASE "{settings.SPIDER_DB_NAME}"'))
                logger.info(f"Database '{settings.SPIDER_DB_NAME}' created successfully.")
    except Exception as e:
        logger.error(f"Failed to check/create database '{settings.SPIDER_DB_NAME}': {e}")

    try:
        SQLModel.metadata.create_all(_sync_engine)
        logger.info("Database tables created/verified successfully.")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise

    try:
        _ensure_run_config_columns()
        logger.info("Run config columns verified successfully.")
    except Exception as e:
        logger.error(f"Failed to verify run config columns: {e}")
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
