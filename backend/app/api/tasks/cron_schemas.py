from pydantic import BaseModel, Field
from typing import Optional, List
import uuid


class CronTaskBase(BaseModel):
    """
    定时任务部分基础共享属性
    """
    spider_id: int = Field(..., description="关联的爬虫 ID")
    cron_expr: str = Field(..., description="Cron 表达式，例如 '*/5 * * * *'")
    description: Optional[str] = Field(None, description="调度任务的说明描述")
    enabled: bool = Field(True, description="是否启用此调度")
    target_node_ids: Optional[List[str]] = Field(None, description="指定执行节点 ID 列表；若为空则分发到公共队列")
    timeout_seconds: int = Field(3600, description="任务执行超时时间（秒），默认1小时")
    owner_id: Optional[uuid.UUID] = Field(None, description="归属用户ID")


class CronTaskCreate(CronTaskBase):
    """
    创建定时任务的请求模型
    """
    pass


class CronTaskResponse(CronTaskBase):
    """
    定时任务返回模型
    """
    job_id: str
    spider_name: str = ""
    next_run_time: Optional[str] = None


class CronTaskUpdate(BaseModel):
    """
    修改定时任务的请求模型
    不传的字段表示不修改
    """
    spider_id: Optional[int] = Field(None, description="关联的爬虫 ID")
    cron_expr: Optional[str] = Field(None, description="Cron 表达式")
    description: Optional[str] = Field(None, description="调度任务说明")
    enabled: Optional[bool] = Field(None, description="是否启用")
    target_node_ids: Optional[List[str]] = Field(None, description="指定执行节点 ID 列表")
    timeout_seconds: Optional[int] = Field(None, description="任务执行超时时间（秒）")


class CronTaskToggle(BaseModel):
    """
    开关切换请求模型
    """
    enabled: bool = Field(..., description="是否启用")
