"""
节点管理路由 — 纯接口层

所有业务逻辑已迁移到 services.py，本模块仅负责路由注册和请求响应。
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, BackgroundTasks
from redis.asyncio import Redis

from app.core.audit.service import audit_log
from app.core.redis import get_redis
from app.api.nodes.schemas import NodeStatus, NodeConfigUpdate
from app.core.schemas.api_response import ApiResponse
from app.api.users.models import User
from app.core.dependencies import require_viewer, require_admin
from app.api.nodes import services

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=ApiResponse[List[NodeStatus]], summary="获取所有活跃节点的负载状态")
async def get_nodes(
    redis: Redis = Depends(get_redis),
    operator: User = Depends(require_viewer),
):
    """
    监控所有 Worker 节点的活跃状态和系统负载。

    :param redis: 注入的 Redis 客户端
    :param operator: 注入的当前操作者
    :return: ApiResponse 包含节点状态列表
    """
    nodes = await services.get_all_nodes(redis)
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
    """
    修改节点配置并持久化到 Redis Hash。

    :param node_id: 节点 UUID
    :param body: 配置更新请求体
    :param background_tasks: 后台任务（审计日志使用）
    :param redis: 注入的 Redis 客户端
    :param operator: 注入的当前操作者（需 admin 角色）
    :return: ApiResponse 成功消息
    """
    message = await services.update_node_config(node_id, body, redis)
    return ApiResponse.success(message=message)


@router.post("/{node_id}/delete", response_model=ApiResponse)
@audit_log(action="DELETE", resource_type="node")
async def uninstall_node(
    node_id: str,
    background_tasks: BackgroundTasks,
    redis: Redis = Depends(get_redis),
    operator: User = Depends(require_admin),
):
    """
    卸载删除节点，清除 Redis 中的状态和配置数据。

    :param node_id: 节点 UUID
    :param background_tasks: 后台任务（审计日志使用）
    :param redis: 注入的 Redis 客户端
    :param operator: 注入的当前操作者（需 admin 角色）
    :return: ApiResponse 成功消息
    """
    message = await services.uninstall_node(node_id, redis)
    return ApiResponse.success(message=message)
