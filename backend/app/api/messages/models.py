from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional

class SystemMessage(SQLModel, table=True):
    __tablename__ = "system_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=200, description="消息标题")
    content: str = Field(description="详细消息内容")
    sender_id: Optional[int] = Field(default=None, index=True, description="发送者ID，为空代表系统自动发送")
    receiver_id: int = Field(index=True, description="接收者ID")
    is_read: bool = Field(default=False, description="是否已被阅读")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="发送时间")
    read_at: Optional[datetime] = Field(default=None, description="阅读时间")
