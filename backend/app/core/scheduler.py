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
    'max_instances': settings.SCHEDULER_MAX_INSTANCES,
    'misfire_grace_time': settings.SCHEDULER_MISFIRE_GRACE_TIME
}

scheduler = AsyncIOScheduler(jobstores=jobstores, job_defaults=job_defaults)

def start_scheduler():
    if not scheduler.running:
        # 挂载每日凌晨 3:00 清理悬挂废弃镜像的任务
        from app.core.container.image_manager import image_manager
        
        # 考虑到 prune_images 包含了阻塞的 Docker 操作，推荐用独立线程或放进事件循环，APScheduler
        # 在 async 模式执行同步函数会在 default executor 运行，可以直接注册
        scheduler.add_job(
            image_manager.prune_images,
            trigger='cron',
            hour=3,
            minute=0,
            id='daily_image_prune',
            replace_existing=True,
            misfire_grace_time=3600
        )
        
        scheduler.start()
        logger.info("APScheduler started with Redis JobStore and registered 'daily_image_prune'.")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("APScheduler shutdown gracefully.")
