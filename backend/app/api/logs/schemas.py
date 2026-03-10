from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class SystemLogBase(BaseModel):
    """
    系统日志基础 Schema
    """
    level: str = Field(default="INFO", description="日志级别(INFO, WARNING, ERROR)")
    action: str = Field(..., description="操作动作")
    user_id: Optional[int] = Field(None, description="执行该操作的用户ID")
    message: str = Field(..., description="详细日志内容")

class SystemLogCreate(SystemLogBase):
    """
    创建系统日志 Schema
    """
    pass

class SystemLogOut(SystemLogBase):
    """
    输出系统日志 Schema
    """
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
