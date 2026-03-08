import asyncio
import socket
import logging
import uuid
import psutil
from datetime import datetime
from typing import Dict, Any
from redis.exceptions import RedisError, ConnectionError, TimeoutError
import json

from app.core.redis import redis_manager
from app.core.timezone import now
from config import settings

logger = logging.getLogger(__name__)

# 获取当前机器的唯一标识
NODE_ID = settings.NODE_ID
HEARTBEAT_INTERVAL = settings.HEARTBEAT_INTERVAL
HEARTBEAT_TTL = settings.HEARTBEAT_TTL
HEARTBEAT_KEY = f"node:status:{NODE_ID}"

def get_local_ip() -> str:
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 并不需要真正连接，只是为了获取本机地址
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

async def get_system_stats() -> Dict[str, Any]:
    """获取当前机器的 CPU 和内存使用率，遵循单一职责."""
    # psutil.cpu_percent 第一次电用会返回 0，所以最好传入 interval=None，但异步下不能阻塞，因此我们靠循环里的间隔来自然平滑
    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    return {
        "node_id": NODE_ID,
        "role": settings.NODE_ROLE,
        "ip": get_local_ip(),
        "cpu_percent": cpu_percent,
        "memory_percent": mem.percent,
        "memory_total_mb": mem.total // (1024 * 1024),
        "memory_used_mb": mem.used // (1024 * 1024),
        "disk_usage": disk.percent,
        "timestamp": now().isoformat()
    }

async def send_heartbeat() -> None:
    """心跳发送逻辑：每隔 HEARTBEAT_INTERVAL 秒发送一次"""
    # 确保初始化调用过一次 psutil，以便后续获取准确率
    psutil.cpu_percent(interval=None)
    logger.info(f"Starting heartbeat for node {NODE_ID}...")

    while True:
        try:
            if not redis_manager.client:
                # 若未初始化，等待主程序初始化
                await asyncio.sleep(1)
                continue

            stats = await get_system_stats()
            value = json.dumps(stats)
            
            # 写入 Redis 并带有 TTL，使用防御性编程处理可能的异常
            await redis_manager.client.set(HEARTBEAT_KEY, value, ex=HEARTBEAT_TTL)
            logger.debug(f"Heartbeat sent for node {NODE_ID}: {stats}")

        except (ConnectionError, TimeoutError, RedisError) as e:
            logger.error(f"Failed to send heartbeat to Redis: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in heartbeat task: {e}")

        await asyncio.sleep(HEARTBEAT_INTERVAL)

async def start_heartbeat_task() -> asyncio.Task:
    """启动后台心跳任务"""
    return asyncio.create_task(send_heartbeat())
