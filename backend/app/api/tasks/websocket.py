"""
任务实时推送 WebSocket 模块

将 WebSocket handler 从 router.py 分离出来，保持单一职责。
通过 Redis Pub/Sub 向前端推送任务实时日志和采集数据。
"""
import asyncio
import logging
from typing import List

from fastapi import WebSocket, WebSocketDisconnect

from app.core.redis import redis_manager
from config import settings

logger = logging.getLogger(__name__)


async def websocket_task_logs(websocket: WebSocket, task_id: str) -> None:
    """
    通过 Redis Pub/Sub 向前端推送任务实时日志。

    连接建立后先从 Redis List 热缓冲回放历史日志，
    再订阅 `log:channel:{task_id}` 频道接收后续实时消息，
    从根本上消除客户端迟到导致的日志丢失问题。

    :param websocket: FastAPI WebSocket 连接对象
    :param task_id: 任务 UUID，用于订阅对应的日志频道
    :return: None
    """
    await websocket.accept()

    if not redis_manager.client:
        await websocket.close(code=1011, reason="Redis inactive")
        return

    channel = f"log:channel:{task_id}"
    hotbuf_key = f"{settings.LOG_HOTBUF_PREFIX}{task_id}"

    # ── 步骤 1：订阅 Pub/Sub（必须在回放热缓冲前完成，避免回放和订阅之间产生新的间隙）
    pubsub = redis_manager.client.pubsub()
    await pubsub.subscribe(channel)
    logger.info("WebSocket client connected for task %s", task_id)

    try:
        # ── 步骤 2：从 Redis List 回放热缓冲（迟到日志补偿）
        try:
            hot_logs: List[bytes] = await redis_manager.client.lrange(hotbuf_key, 0, -1)
            if hot_logs:
                for raw in hot_logs:
                    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    await websocket.send_text(text)
                logger.info(
                    "WebSocket replayed %d hotbuf log(s) for task %s",
                    len(hot_logs), task_id,
                )
        except Exception as replay_err:
            logger.warning("Failed to replay hotbuf for task %s: %s", task_id, replay_err)

        # ── 步骤 3：持续接收 Pub/Sub 实时日志
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.1
            )
            if message and message["type"] == "message":
                data = message["data"]
                text = data.decode("utf-8") if isinstance(data, bytes) else data
                await websocket.send_text(text)
            else:
                await asyncio.sleep(0.01)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected for task %s", task_id)
    except Exception as e:
        logger.error("WebSocket error for task %s: %s", task_id, e)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()


async def websocket_task_data(websocket: WebSocket, task_id: str) -> None:
    """
    通过 Redis Pub/Sub 向前端实时推送任务采集数据。

    订阅 `data:channel:{task_id}` 频道，将消息实时转发到 WebSocket 客户端。
    当客户端断开连接时自动取消订阅并清理资源。

    :param websocket: FastAPI WebSocket 连接对象
    :param task_id: 任务 UUID，用于订阅对应的数据频道
    :return: None
    """
    await websocket.accept()

    if not redis_manager.client:
        await websocket.close(code=1011, reason="Redis inactive")
        return

    pubsub = redis_manager.client.pubsub()
    channel = f"data:channel:{task_id}"
    await pubsub.subscribe(channel)
    logger.info("WebSocket data client connected for task %s", task_id)

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.1
            )
            if message and message["type"] == "message":
                data = message["data"]
                text = data.decode("utf-8") if isinstance(data, bytes) else data
                await websocket.send_text(text)
            else:
                await asyncio.sleep(0.01)
    except WebSocketDisconnect:
        logger.info("WebSocket data client disconnected for task %s", task_id)
    except Exception as e:
        logger.error("WebSocket data error for task %s: %s", task_id, e)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
