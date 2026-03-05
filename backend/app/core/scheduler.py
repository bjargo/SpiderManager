import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.redis import RedisJobStore

from config import settings

logger = logging.getLogger(__name__)

# 使用 Redis 作为唯一的持久化后端
jobstores = {
    'default': RedisJobStore(
        jobs_key='apscheduler.jobs',
        run_times_key='apscheduler.run_times',
        # 直接使用 config 中拆分后的主机和端口字段
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT
    )
}

# ⚠️ 不要为 AsyncIOScheduler 配置 ThreadPoolExecutor！
# AsyncIOScheduler 本身已使用 AsyncIOExecutor（默认），能在当前事件循环中直接 await 异步 job。
# ThreadPoolExecutor 无法 await coroutine，会触发 "coroutine was never awaited" 并阻塞事件循环。
job_defaults = {
    'coalesce': False,
    'max_instances': settings.SCHEDULER_MAX_INSTANCES
}

scheduler = AsyncIOScheduler(jobstores=jobstores, job_defaults=job_defaults)

def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started with Redis JobStore.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("APScheduler shutdown gracefully.")
