from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import Text
from datetime import datetime

from app.common.timezone import now


class TaskLog(SQLModel, table=True):
    """任务日志行记录"""
    __tablename__ = "task_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True, description="关联任务 ID")
    content: str = Field(sa_column=Column(Text, nullable=False), description="日志内容")
    created_at: datetime = Field(default_factory=now, description="日志时间戳")
