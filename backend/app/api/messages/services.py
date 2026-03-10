"""
消息管理业务逻辑层（异步版本）

将 router.py 中的消息管理逻辑全部迁移至此。
替代原有的同步 service + CRUD 层。
"""
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.messages.models import SystemMessage
from app.api.messages import schemas
from app.api.users.models import User
from app.core.enums import UserRole
from app.core.timezone import now

logger = logging.getLogger(__name__)


async def send_message(
    message_in: schemas.SystemMessageCreate,
    session: AsyncSession,
) -> SystemMessage:
    """
    发送一条系统消息。

    :param message_in: 消息创建请求体（title、content、receiver_id、sender_id）
    :param session: 异步数据库会话
    :return: 创建后的 SystemMessage ORM 对象
    :raises HTTPException: 400 — 接收用户 ID 无效
    """
    if message_in.receiver_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid receiver ID")

    db_msg = SystemMessage(
        title=message_in.title,
        content=message_in.content,
        receiver_id=message_in.receiver_id,
        sender_id=message_in.sender_id,
    )
    session.add(db_msg)
    await session.commit()
    await session.refresh(db_msg)
    return db_msg


async def get_user_messages(
    user_id: int,
    skip: int,
    limit: int,
    is_read: bool | None,
    operator: User,
    session: AsyncSession,
) -> list[SystemMessage]:
    """
    获取指定用户的系统消息列表。

    非 admin 用户不显示已删除消息。

    :param user_id: 目标用户 ID
    :param skip: 跳过记录数
    :param limit: 返回记录数上限
    :param is_read: 已读/未读筛选（None 表示不筛选）
    :param operator: 当前操作者
    :param session: 异步数据库会话
    :return: SystemMessage ORM 对象列表
    """
    stmt = select(SystemMessage).where(SystemMessage.receiver_id == user_id)

    if getattr(operator, "role", None) != UserRole.admin:
        stmt = stmt.where(SystemMessage.is_deleted == False)

    if is_read is not None:
        stmt = stmt.where(SystemMessage.is_read == is_read)

    stmt = stmt.order_by(SystemMessage.created_at.desc()).offset(skip).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def read_message(
    message_id: int,
    session: AsyncSession,
) -> SystemMessage:
    """
    标记指定消息为已读并返回消息内容。

    :param message_id: 消息主键 ID
    :param session: 异步数据库会话
    :return: 更新后的 SystemMessage ORM 对象
    :raises HTTPException: 404 — 消息不存在或已删除
    """
    msg = await session.get(SystemMessage, message_id)
    if not msg or msg.is_deleted:
        raise HTTPException(status_code=404, detail="Message not found")

    msg.is_read = True
    if not msg.read_at:
        msg.read_at = now()
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def delete_message(
    message_id: int,
    session: AsyncSession,
) -> None:
    """
    软删除指定消息（标记 is_deleted = True）。

    :param message_id: 消息主键 ID
    :param session: 异步数据库会话
    :return: None
    :raises HTTPException: 404 — 消息不存在或已删除
    """
    msg = await session.get(SystemMessage, message_id)
    if not msg or msg.is_deleted:
        raise HTTPException(status_code=404, detail="Message not found")

    msg.is_deleted = True
    session.add(msg)
    await session.commit()
