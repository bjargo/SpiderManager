"""
消息管理路由 — 全异步
"""
from typing import List, Optional
from datetime import datetime

from app.core.timezone import now

from fastapi import APIRouter, Depends, Query, Path, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.messages.models import SystemMessage
from app.api.messages import schemas
from app.api.users.models import User
from app.core.dependencies import require_viewer
from app.db.database import get_async_session

router = APIRouter()


@router.post("/", response_model=schemas.SystemMessageOut, summary="发送系统消息")
async def send_message(
    message_in: schemas.SystemMessageCreate,
    session: AsyncSession = Depends(get_async_session)
):
    if message_in.receiver_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid receiver ID")

    db_msg = SystemMessage(
        title=message_in.title, content=message_in.content,
        receiver_id=message_in.receiver_id, sender_id=message_in.sender_id,
    )
    session.add(db_msg)
    await session.commit()
    await session.refresh(db_msg)
    return db_msg


@router.get("/user/{user_id}", response_model=List[schemas.SystemMessageOut], summary="查询用户的系统消息")
async def read_user_messages(
    user_id: int = Path(...),
    skip: int = Query(0), limit: int = Query(100),
    is_read: Optional[bool] = Query(None),
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_viewer)
):
    stmt = select(SystemMessage).where(SystemMessage.receiver_id == user_id)
    # Filter out deleted messages unless user is an admin
    from app.core.enums import UserRole
    if getattr(operator, "role", None) != UserRole.admin:
        stmt = stmt.where(SystemMessage.is_deleted == False)

    if is_read is not None:
        stmt = stmt.where(SystemMessage.is_read == is_read)
    stmt = stmt.order_by(SystemMessage.created_at.desc()).offset(skip).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/{message_id}/read", response_model=schemas.SystemMessageOut, summary="阅读系统消息")
async def read_message(
    message_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session)
):
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


@router.post("/{message_id}/delete", summary="删除系统消息")
async def delete_message(
    message_id: int = Path(...),
    session: AsyncSession = Depends(get_async_session)
):
    msg = await session.get(SystemMessage, message_id)
    if not msg or msg.is_deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.is_deleted = True
    session.add(msg)
    await session.commit()
    return {"message": "Message deleted successfully"}
