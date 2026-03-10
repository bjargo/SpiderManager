"""
消息管理路由 — 纯接口层

所有业务逻辑已迁移到 services.py，本模块仅负责路由注册和请求响应。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.messages import schemas
from app.api.messages import services
from app.api.users.models import User
from app.core.dependencies import require_viewer
from app.db.database import get_async_session

router = APIRouter()


@router.post("/", response_model=schemas.SystemMessageOut, summary="发送系统消息")
async def send_message(
    message_in: schemas.SystemMessageCreate,
    session: AsyncSession = Depends(get_async_session),
) -> schemas.SystemMessageOut:
    """
    发送一条系统消息。

    :param message_in: 消息创建请求体
    :param session: 注入的数据库会话
    :return: 创建后的消息对象
    """
    return await services.send_message(message_in, session)


@router.get("/user/{user_id}", response_model=List[schemas.SystemMessageOut], summary="查询用户的系统消息")
async def read_user_messages(
    user_id: int = Path(...),
    skip: int = Query(0),
    limit: int = Query(100),
    is_read: Optional[bool] = Query(None),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer),
):
    """
    获取指定用户的系统消息列表。

    :param user_id: 目标用户 ID
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param is_read: 已读/未读筛选
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者
    :return: 消息列表
    """
    return await services.get_user_messages(user_id, skip, limit, is_read, operator, session)


@router.post("/{message_id}/read", response_model=schemas.SystemMessageOut, summary="阅读系统消息")
async def read_message(
    message_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
):
    """
    标记指定消息为已读。

    :param message_id: 消息主键 ID
    :param session: 注入的数据库会话
    :return: 更新后的消息对象
    """
    return await services.read_message(message_id, session)


@router.post("/{message_id}/delete", summary="删除系统消息")
async def delete_message(
    message_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session),
):
    """
    软删除指定消息。

    :param message_id: 消息主键 ID
    :param session: 注入的数据库会话
    :return: 删除成功消息
    """
    await services.delete_message(message_id, session)
    return {"message": "Message deleted successfully"}
