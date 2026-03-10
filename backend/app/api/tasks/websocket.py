"""
任务实时推送 WebSocket 模块

将 WebSocket handler 从 router.py 分离出来，保持单一职责。
通过 Redis Pub/Sub 向前端推送任务实时日志和采集数据。
"""
import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.core.redis import redis_manager

logger = logging.getLogger(__name__)


async def websocket_task_logs(websocket: WebSocket, task_id: str) -> None:
    """
    通过 Redis Pub/Sub 向前端推送任务实时日志。

    订阅 `log:channel:{task_id}` 频道，将消息实时转发到 WebSocket 客户端。
    当客户端断开连接时自动取消订阅并清理资源。

    :param websocket: FastAPI WebSocket 连接对象
    :param task_id: 任务 UUID，用于订阅对应的日志频道
    :return: None
    """
    await websocket.accept()

    if not redis_manager.client:
        await websocket.close(code=1011, reason="Redis inactive")
        return

    pubsub = redis_manager.client.pubsub()
    channel = f"log:channel:{task_id}"
    await pubsub.subscribe(channel)
    logger.info("WebSocket client connected for task %s", task_id)

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
