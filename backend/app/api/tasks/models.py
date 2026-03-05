from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime

from app.common.timezone import now


class SpiderTask(SQLModel, table=True):
    """爬虫任务执行记录"""
    __tablename__ = "spider_tasks"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True, unique=True, description="UUID 任务 ID")
    spider_id: int = Field(index=True, description="关联爬虫 ID")
    spider_name: str = Field(description="快照：爬虫名称")

    status: str = Field(
        default="pending", index=True,
        description="任务状态: pending / running / success / failed / timeout / error"
    )
    node_id: Optional[str] = Field(default=None, description="执行节点 ID")
    command: Optional[str] = Field(default=None, description="执行命令")
    error_detail: Optional[str] = Field(default=None, description="异常信息")

    created_at: datetime = Field(default_factory=now, description="任务创建时间")
    started_at: Optional[datetime] = Field(default=None, description="任务开始时间")
    finished_at: Optional[datetime] = Field(default=None, description="任务结束时间")
