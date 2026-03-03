from sqlmodel import Session
from typing import List, Optional
from fastapi import HTTPException
from . import schemas, crud
from .models import SystemMessage

class MessagesService:
    """
    消息系统核心业务逻辑层
    负责调度 CRUD 和处理各类业务规则
    """

    @staticmethod
    def send_message(db: Session, message_data: schemas.SystemMessageCreate) -> SystemMessage:
        """
        发送一条系统消息
        """
        # 初步的业务校验（如判断接收用户是否存在等，依赖后续其它模块的对接，暂时仅抛出异常模板）
        if message_data.receiver_id <= 0:
            raise HTTPException(status_code=400, detail="Invalid receiver ID")
            
        return crud.MessagesCRUD.create_message(db=db, message_in=message_data)

    @staticmethod
    def get_user_messages(
        db: Session, user_id: int, skip: int = 0, limit: int = 100, is_read: Optional[bool] = None
    ) -> List[SystemMessage]:
        """
        获取用户的消息列表
        """
        return crud.MessagesCRUD.get_messages_for_user(db=db, user_id=user_id, skip=skip, limit=limit, is_read=is_read)

    @staticmethod
    def read_message(db: Session, message_id: int) -> SystemMessage:
        """
        标记消息为已读并返回内容
        """
        message = crud.MessagesCRUD.get_message_by_id(db=db, message_id=message_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
        
        updated_message = crud.MessagesCRUD.update_message_status(
            db=db, 
            message_id=message_id, 
            status_update=schemas.SystemMessageUpdate(is_read=True)
        )
        return updated_message

    @staticmethod
    def delete_message(db: Session, message_id: int):
        """
        删除消息
        """
        success = crud.MessagesCRUD.delete_message(db=db, message_id=message_id)
        if not success:
            raise HTTPException(status_code=404, detail="Message not found")
