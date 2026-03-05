from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional

from app.common.timezone import now

class SystemLog(SQLModel, table=True):
    __tablename__ = "system_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    level: str = Field(default="INFO", index=True, description="日志级别(INFO, WARNING, ERROR)")
    action: str = Field(max_length=100, index=True, description="操作动作")
    user_id: Optional[int] = Field(default=None, index=True, description="执行该操作的用户ID，允许为空（系统操作）")
    message: str = Field(description="详细日志内容")
    created_at: datetime = Field(default_factory=now, description="创建时间")
