"""
节点管理业务逻辑层

将 router.py 中的 Redis 扫描、节点配置更新和节点删除逻辑全部迁移至此。
"""
import json
import logging

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.api.nodes.schemas import NodeStatus, NodeConfigUpdate
from app.core.timezone import now as now_tz

logger = logging.getLogger(__name__)


async def get_all_nodes(redis: Redis) -> list[NodeStatus]:
    """
    通过 Redis SCAN 命令扫描所有活跃节点的负载状态。

    使用 SCAN 而非 KEYS 命令，防止阻塞 Redis。
    每个节点的状态由 `node:status:{node_id}` 键存储（心跳数据），
    持久化配置由 `node:config:{node_id}` Hash 存储。

    :param redis: Redis 客户端
    :return: NodeStatus 列表（异常情况返回空列表以保证高可用）
    """
    nodes: list[NodeStatus] = []
    try:
        cursor = 0
        match_pattern = "node:status:*"

        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=match_pattern, count=100)

            if keys:
                values = await redis.mget(keys)
                for key, val in zip(keys, values):
                    if val:
                        node = await _parse_node_status(key, val, redis)
                        if node:
                            nodes.append(node)

            if int(cursor) == 0:
                break

    except RedisError as e:
        logger.error(f"Redis error when querying node status: {e}")

    return nodes


async def _parse_node_status(key: bytes | str, val: bytes | str, redis: Redis) -> NodeStatus | None:
    """
    解析单个节点的状态数据和持久化配置。

    :param key: Redis 键名（node:status:{node_id}）
    :param val: Redis 值（JSON 字符串）
    :param redis: Redis 客户端（用于读取配置 Hash）
    :return: NodeStatus 对象，解析失败返回 None
    """
    try:
        key_str = key.decode("utf-8") if isinstance(key, bytes) else key
        data = json.loads(val)

        nid = data.get("node_id", key_str.split(":")[-1])
        config_data = await redis.hgetall(f"node:config:{nid}")

        # bytes -> str decoding
        c_data = {
            (k.decode("utf-8") if isinstance(k, bytes) else k): (v.decode("utf-8") if isinstance(v, bytes) else v)
            for k, v in config_data.items()
        }

        # 旧数据向后兼容
        custom_name = c_data.get("name")
        if not custom_name:
            old_name = await redis.get(f"node:name:{nid}")
            custom_name = old_name.decode("utf-8") if isinstance(old_name, bytes) else old_name

        # 解析 bool / int 字段
        is_enabled = c_data.get("enabled", "true").lower() == "true"
        try:
            max_runners = int(c_data.get("max_runners", "1"))
        except ValueError:
            max_runners = 1

        return NodeStatus(
            node_id=nid,
            name=custom_name if custom_name else nid[:8],
            role=data.get("role", "worker"),
            ip=data.get("ip", "127.0.0.1"),
            cpu_usage=data.get("cpu_percent", 0.0),
            mem_usage=data.get("memory_percent", 0.0),
            disk_usage=data.get("disk_usage", 0.0),
            memory_total_mb=data.get("memory_total_mb", 0),
            memory_used_mb=data.get("memory_used_mb", 0),
            last_heartbeat=data.get("timestamp", now_tz().isoformat()),
            status="online",
            mac_address=c_data.get("mac_address", ""),
            enabled=is_enabled,
            max_runners=max_runners,
        )
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse node status from Redis key {key}: {e}")
        return None
    except ValueError as e:
        logger.warning(f"Validation error for node status data: {e}")
        return None


async def update_node_config(
    node_id: str,
    body: NodeConfigUpdate,
    redis: Redis,
) -> str:
    """
    修改节点配置字典并持久化到 Redis Hash。

    :param node_id: 节点 UUID
    :param body: 配置更新请求体（name、mac_address、enabled、max_runners）
    :param redis: Redis 客户端
    :return: 成功消息字符串
    :raises HTTPException: 500 — Redis 操作失败
    """
    config_key = f"node:config:{node_id}"
    mapping = {
        "name": body.name,
        "mac_address": body.mac_address,
        "enabled": "true" if body.enabled else "false",
        "max_runners": str(body.max_runners),
    }

    try:
        await redis.hset(config_key, mapping=mapping)
        return f"节点 {node_id} 配置已更新"
    except RedisError as e:
        logger.error(f"Redis error when updating node {node_id} config: {e}")
        raise HTTPException(status_code=500, detail="保存节点配置失败 (Redis异常)")


async def uninstall_node(
    node_id: str,
    redis: Redis,
) -> str:
    """
    卸载删除节点，清除 Redis 中的状态信息和持久化配置。

    :param node_id: 节点 UUID
    :param redis: Redis 客户端
    :return: 成功消息字符串
    :raises HTTPException: 500 — Redis 操作失败
    """
    status_key = f"node:status:{node_id}"
    config_key = f"node:config:{node_id}"
    old_name_key = f"node:name:{node_id}"

    try:
        await redis.delete(status_key, config_key, old_name_key)
        return f"节点 {node_id} 已成功删除"
    except RedisError as e:
        logger.error(f"Redis error when deleting node {node_id}: {e}")
        raise HTTPException(status_code=500, detail="删除节点失败 (Redis异常)")
