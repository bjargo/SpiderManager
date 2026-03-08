from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class SystemMessageBase(BaseModel):
    """
    系统消息基础 Schema
    """
    title: str = Field(..., description="消息标题")
    content: str = Field(..., description="消息内容详情")
    receiver_id: int = Field(..., description="接收者用户ID")
    sender_id: Optional[int] = Field(None, description="发送者用户ID，为空代表系统发送")

class SystemMessageCreate(SystemMessageBase):
    """
    创建系统消息 Schema
    """
    pass

class SystemMessageUpdate(BaseModel):
    """
    更新系统消息状态 Schema
    """
    is_read: bool = Field(..., description="是否已读标记")

class SystemMessageOut(SystemMessageBase):
    """
    输出系统消息 Schema
    """
    id: int
    is_read: bool

    created_at: datetime
    read_at: Optional[datetime] = None
    is_deleted: bool = False

    class Config:
        orm_mode = True
