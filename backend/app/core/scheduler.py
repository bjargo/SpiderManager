import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.redis import RedisJobStore
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from config import settings
from app.core.redis import redis_manager

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


# ── 分布式 Leader 选举 ───────────────────────────────────────────────────────
# 集群中可能存在多个 Master 节点，但同一个定时任务只允许被触发一次。
# 通过 Redis 分布式锁选举唯一的 Leader：只有持锁节点启动并维持 APScheduler。
LEADER_KEY = "master:scheduler:leader"
LEADER_TTL = 15            # 锁的过期时间（秒），Leader 崩溃后备用节点最多等待此时长即可接管
LEADER_RENEW_INTERVAL = 5  # 抢锁/续约轮询间隔（秒），须显著小于 LEADER_TTL

# 原子续约：仅当锁仍归属本节点时才刷新 TTL，避免误续他人已抢走的锁
_RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

# 原子释放：仅当锁仍归属本节点时才删除，便于备用节点立即接管
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

_is_leader = False
_leader_task: "asyncio.Task | None" = None


async def _acquire_or_renew_leader() -> bool:
    """
    抢占或续约 Leader 锁。

    返回 ``True`` 表示本节点当前确实持有 Leader 锁：
      - 首次通过 ``SET key node_id NX EX ttl`` 原子抢锁成功；
      - 或锁已归属本节点，通过 Lua 脚本原子续期 TTL。
    任何 Redis 异常都视为「未持锁」，由上层据此停止调度，保证安全性优先。
    """
    client = redis_manager.client
    if client is None:
        return False

    node_id = settings.NODE_ID
    try:
        # NX：仅在键不存在时写入，天然保证并发下只有一个节点成功
        acquired = await client.set(LEADER_KEY, node_id, nx=True, ex=LEADER_TTL)
        if acquired:
            return True
        # 抢锁失败时尝试续约（例如本节点已是 Leader，或 Redis 抖动后重连）
        renewed = await client.eval(_RENEW_SCRIPT, 1, LEADER_KEY, node_id, LEADER_TTL)
        return bool(renewed)
    except (RedisConnectionError, RedisTimeoutError, RedisError) as e:
        logger.error("Leader 选举访问 Redis 失败: %s", e)
        return False


async def _leader_election_loop() -> None:
    """
    Leader 选举与心跳续约循环。

    仅当成功持有 Leader 锁时启动 APScheduler；一旦失去锁则立即停止调度。
    这样备用 Master 可在 ``LEADER_TTL`` 内秒级接管，且集群模式下定时任务不会重复触发。
    """
    global _is_leader
    logger.info(
        "节点 %s 启动 Leader 选举循环 (key=%s, ttl=%ss, renew=%ss)",
        settings.NODE_ID, LEADER_KEY, LEADER_TTL, LEADER_RENEW_INTERVAL,
    )

    while True:
        held = await _acquire_or_renew_leader()

        if held and not _is_leader:
            _is_leader = True
            start_scheduler()
            logger.info("节点 %s 当选 Leader，已启动 APScheduler。", settings.NODE_ID)
        elif not held and _is_leader:
            _is_leader = False
            shutdown_scheduler()
            logger.warning("节点 %s 失去 Leader 锁，已停止 APScheduler 调度。", settings.NODE_ID)

        await asyncio.sleep(LEADER_RENEW_INTERVAL)


async def start_leader_election() -> asyncio.Task:
    """启动 Leader 选举后台任务，返回任务句柄供应用生命周期管理。"""
    global _leader_task
    _leader_task = asyncio.create_task(_leader_election_loop())
    return _leader_task


async def stop_leader_election(task: "asyncio.Task | None" = None) -> None:
    """停止 Leader 选举循环，并主动释放锁以便备用节点立即接管。"""
    global _leader_task, _is_leader

    target = task or _leader_task
    if target is not None:
        target.cancel()
        try:
            await target
        except asyncio.CancelledError:
            pass
    _leader_task = None

    client = redis_manager.client
    if _is_leader and client is not None:
        try:
            await client.eval(_RELEASE_SCRIPT, 1, LEADER_KEY, settings.NODE_ID)
            logger.info("节点 %s 已释放 Leader 锁。", settings.NODE_ID)
        except (RedisConnectionError, RedisTimeoutError, RedisError) as e:
            logger.error("释放 Leader 锁失败: %s", e)

    _is_leader = False
    shutdown_scheduler()


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
