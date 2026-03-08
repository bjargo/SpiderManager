from sqlmodel import Session, select
from typing import List, Optional
from datetime import datetime
from app.core.timezone import now
from . import models, schemas

class MessagesCRUD:
    """
    消息系统 CRUD 数据访问层
    封闭底层数据库操作，对外提供统一接口
    """

    @staticmethod
    def create_message(db: Session, message_in: schemas.SystemMessageCreate) -> models.SystemMessage:
        """
        向数据库写入一条新的消息记录
        """
        db_message = models.SystemMessage(
            title=message_in.title,
            content=message_in.content,
            receiver_id=message_in.receiver_id,
            sender_id=message_in.sender_id
        )
        db.add(db_message)
        db.commit()
        db.refresh(db_message)
        return db_message

    @staticmethod
    def get_messages_for_user(
        db: Session, user_id: int, skip: int = 0, limit: int = 100, is_read: Optional[bool] = None
    ) -> List[models.SystemMessage]:
        """
        查询指定用户的消息列表，支持按已读状态过滤
        """
        statement = select(models.SystemMessage).where(models.SystemMessage.receiver_id == user_id)
        if is_read is not None:
            statement = statement.where(models.SystemMessage.is_read == is_read)
        statement = statement.order_by(models.SystemMessage.created_at.desc()).offset(skip).limit(limit)
        return list(db.exec(statement).all())

    @staticmethod
    def get_message_by_id(db: Session, message_id: int) -> Optional[models.SystemMessage]:
        """
        根据消息 ID 查询
        """
        return db.get(models.SystemMessage, message_id)

    @staticmethod
    def update_message_status(
        db: Session, message_id: int, status_update: schemas.SystemMessageUpdate
    ) -> Optional[models.SystemMessage]:
        """
        更新消息状态（如标记为已读）
        """
        db_message = MessagesCRUD.get_message_by_id(db, message_id)
        if db_message:
            db_message.is_read = status_update.is_read
            if status_update.is_read and not db_message.read_at:
                db_message.read_at = now()
            db.add(db_message)
            db.commit()
            db.refresh(db_message)
        return db_message

    @staticmethod
    def delete_message(db: Session, message_id: int) -> bool:
        """
        删除指定的系统消息
        """
        db_message = MessagesCRUD.get_message_by_id(db, message_id)
        if db_message:
            db.delete(db_message)
            db.commit()
            return True
        return False
