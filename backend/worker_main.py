import asyncio
import logging
import signal
import sys

from app.common.redis import redis_manager
from app.worker.heartbeat import start_heartbeat_task
from app.worker.executor import start_task_listener

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger(__name__)

shutdown_event = asyncio.Event()

def handle_sigterm(signum, frame):
    logger.info(f"Received signal {signum}, scheduling shutdown...")
    shutdown_event.set()

async def main():
    # 注册优雅退出信号
    signal.signal(signal.SIGINT, handle_sigterm)
    signal.signal(signal.SIGTERM, handle_sigterm)

    # 1. 初始化依赖 (Redis 等)
    logger.info("Initializing worker dependencies...")
    await redis_manager.init_pool()

    # 2. 启动后台任务 (例如心跳和队列监听)
    heartbeat_task = await start_heartbeat_task()
    task_listener_task = await start_task_listener()
    
    logger.info("Worker started successfully. Waiting for shutdown signal...")
    
    # 阻塞直到收到退出信号
    await shutdown_event.wait()
    
    logger.info("Worker shutting down...")
    heartbeat_task.cancel()
    task_listener_task.cancel()
    
    # 清理依赖
    await redis_manager.close_pool()
    logger.info("Worker shutdown complete.")

if __name__ == "__main__":
    # Windows 下如果出现 NotImplementedError 需要配置对应的 event loop policy
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())
