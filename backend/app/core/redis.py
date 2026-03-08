import logging
from typing import AsyncGenerator

from redis.asyncio import Redis, ConnectionPool
from redis.exceptions import RedisError, ConnectionError, TimeoutError

from config import settings

logger = logging.getLogger(__name__)

class RedisManager:
    """
    高可用的异步 Redis 连接池管理器，遵循单一职责(SRP)。
    """
    def __init__(self) -> None:
        self.pool: ConnectionPool | None = None
        self.client: Redis | None = None

    async def init_pool(self) -> None:
        """初始化异步 Redis 连接池，提供防御性编程边界处理。"""
        if self.pool is not None:
            logger.warning("Redis pool is already initialized.")
            return

        try:
            # 引入依赖并验证连接可用性
            self.pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                decode_responses=True,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=5.0,
                retry_on_timeout=True,          # 提高可用性
            )
            self.client = Redis(connection_pool=self.pool)
            
            # 使用 ping 探测连通性，提前发现异常
            await self.client.ping()
            logger.info("Redis connection pool initialized successfully.")
        except (ConnectionError, TimeoutError, RedisError, ValueError) as e:
            logger.error(f"Failed to initialize Redis connection pool: {e}")
            # 进行故障隔离，异常抛出至上层捕获
            raise RuntimeError(f"Redis backend initialized failed. Error: {e}") from e

    async def close_pool(self) -> None:
        """优雅释放资源"""
        if self.client:
            try:
                # 兼容较低版本的 redis-py 或者直接用 aclose
                if hasattr(self.client, 'aclose'):
                    await self.client.aclose()
                else:
                    await self.client.close()
            except Exception as e:
                logger.error(f"Failed to close Redis client gracefully: {e}")
            finally:
                self.client = None
                self.pool = None
                logger.info("Redis connection pool closed.")

# 全局单例管理器
redis_manager = RedisManager()

async def get_redis() -> AsyncGenerator[Redis, None]:
    """
    FastAPI Depends 使用的依赖注入 Redis 客户端，降低路由耦合。
    确保在使用时可正常获取连接并在结束后从 yield 返回。
    """
    if redis_manager.client is None:
        logger.error("Redis client is requested but not initialized.")
        raise ConnectionError("Redis client is not initialized. Make sure init_pool is called at startup.")
    
    try:
        # yield 将连接状态暴露给 caller
        yield redis_manager.client
    except (ConnectionError, TimeoutError) as e:
        # 精准异常捕获与日志记录
        logger.error(f"Network error when interacting with Redis: {e}")
        raise
    except RedisError as e:
        logger.error(f"Redis inner error when interacting with Redis: {e}")
        raise
