from pydantic import BaseModel, Field
from typing import Optional, List
from app.api.spiders.schemas import SpiderTaskOut

class TaskRequest(BaseModel):
    """
    爬虫任务分发请求模型
    """
    task_id: str = Field(..., description="统一的任务追溯 ID")
    spider_id: int = Field(..., description="关联的爬虫 ID，用于查询爬虫配置与代码源")
    script_path: str = Field(..., description="具体的爬虫入口脚本，例如 'main.py'或'spiders/test.py'")
    target_node_ids: Optional[List[str]] = Field(None, description="指定执行此任务的节点 ID 列表；若为空，则分发到公共队列")
    timeout_seconds: int = Field(3600, description="任务执行超时时间（秒），默认1小时")

class TaskListResponse(BaseModel):
    items: List[SpiderTaskOut]
    total: int
