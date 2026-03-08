import json
import logging
from datetime import datetime

from app.core.timezone import now as now_tz
from typing import List

from fastapi import APIRouter, Depends, BackgroundTasks
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.audit.service import audit_log

from app.core.redis import get_redis
from app.api.nodes.schemas import NodeStatus, NodeConfigUpdate
from app.core.schemas.api_response import ApiResponse
from app.api.users.models import User
from app.core.dependencies import require_viewer, require_developer, require_admin

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("", response_model=ApiResponse[List[NodeStatus]], summary="获取所有活跃节点的负载状态")
async def get_nodes(
    redis: Redis = Depends(get_redis),
    operator: User = Depends(require_viewer),
):
    """
    监控所有 Worker 节点的活跃状态和系统负载。
    通过使用 SCAN 命令扫描 `node:status:*`，解析并返回节点列表。
    为保证高可用，进行了防御性的异常处理。
    """
    nodes = []
    try:
        # 使用 SCAN 命令而不是 KEYS 以防阻塞 Redis
        cursor = 0  # int 0 for aioredis / redis-py 4+
        match_pattern = "node:status:*"
        
        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=match_pattern, count=100)
            
            if keys:
                # 批量获取 keys 对应的值
                values = await redis.mget(keys)
                
                for key, val in zip(keys, values):
                    if val:
                        try:
                            # 兼容 key 返回 bytes 还是 str
                            key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                            data = json.loads(val)
                            
                            # 从 Redis 获取节点持久化配置 (node:config:{node_id})
                            nid = data.get("node_id", key_str.split(":")[-1])
                            config_key = f"node:config:{nid}"
                            config_data = await redis.hgetall(config_key)
                            
                            # bytes -> str decoding map for HGETALL
                            c_data = {k.decode('utf-8') if isinstance(k, bytes) else k: v.decode('utf-8') if isinstance(v, bytes) else v for k, v in config_data.items()}
                            
                            # 旧数据的向后兼容 (如果不存在 config 数据，尝试去取一下旧的 name)
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

                            node_status = NodeStatus(
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
                                max_runners=max_runners
                            )
                            nodes.append(node_status)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse node status from Redis key {key}: {e}")
                        except ValueError as e:
                            logger.warning(f"Validation error for node status data: {e}")
            
            # 当 cursor 变为 0 (数字或字符串) 时循环结束
            if int(cursor) == 0:
                break
                        
    except RedisError as e:
        logger.error(f"Redis error when querying node status: {e}")
        # 异常情况返回空列表而不是引发500，以满足高可用和优雅降级
        # 或根据项目需求抛出 HTTPException
        pass
        
    return ApiResponse.success(data=nodes)

@router.post("/{node_id}/config", response_model=ApiResponse)
@audit_log(action="UPDATE", resource_type="node")
async def update_node_config(
    node_id: str,
    body: NodeConfigUpdate,
    background_tasks: BackgroundTasks,
    redis: Redis = Depends(get_redis),
    operator: User = Depends(require_admin),
):
    """修改节点配置字典并持久化到 Redis Hash (遵循只用 POST 原则)"""
    config_key = f"node:config:{node_id}"
    
    mapping = {
        "name": body.name,
        "mac_address": body.mac_address,
        "enabled": "true" if body.enabled else "false",
        "max_runners": str(body.max_runners)
    }
    
    try:
        await redis.hset(config_key, mapping=mapping)
        return ApiResponse.success(message=f"节点 {node_id} 配置已更新")
    except RedisError as e:
        logger.error(f"Redis error when updating node {node_id} config: {e}")
        return ApiResponse.error(code=500, message="保存节点配置失败 (Redis异常)")

@router.post("/{node_id}/delete", response_model=ApiResponse)
@audit_log(action="DELETE", resource_type="node")
async def uninstall_node(
    node_id: str,
    background_tasks: BackgroundTasks,
    redis: Redis = Depends(get_redis),
    operator: User = Depends(require_admin),
):
    """卸载删除节点，清除相关的状态信息和持久化配置 (遵循只用 POST 原则)"""
    status_key = f"node:status:{node_id}"
    config_key = f"node:config:{node_id}"
    old_name_key = f"node:name:{node_id}" # 顺手清下遗留数据
    
    try:
        await redis.delete(status_key, config_key, old_name_key)
        return ApiResponse.success(message=f"节点 {node_id} 已成功删除")
    except RedisError as e:
        logger.error(f"Redis error when deleting node {node_id}: {e}")
        return ApiResponse.error(code=500, message="删除节点失败 (Redis异常)")
