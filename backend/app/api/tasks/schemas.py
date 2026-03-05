from pydantic import BaseModel, Field
from typing import Any, Optional, List
from app.api.spiders.schemas import SpiderTaskOut


class DataIngestRequest(BaseModel):
    """
    数据 Ingestion 请求体。
    爬虫在采集到数据后，通过此接口将数据推入 Redis 队列，
    由下游消费者异步落库，实现采集与存储的解耦。
    """
    table_name: str = Field(..., min_length=1, max_length=128, description="目标数据表名")
    data: list[dict[str, Any]] = Field(..., min_length=1, description="待写入的数据记录列表")


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
