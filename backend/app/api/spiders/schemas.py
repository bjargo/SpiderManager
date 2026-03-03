from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class SpiderBase(BaseModel):
    name: str = Field(..., description="爬虫名称")
    description: Optional[str] = Field(None, description="爬虫描述")
    
    # 关联项目的配置 (使用刚才建立的 Project 元数据中的信息)
    project_id: str = Field(..., description="关联的项目ID")
    source_type: str = Field(..., description="代码源类型: MINIO 或 GIT")
    source_url: str = Field(..., description="MinIO Key 或 Git URL")
    
    language: Optional[str] = Field("python:3.11-slim", description="Docker 基础镜像名称")
    command: Optional[str] = Field(None, description="执行命令，如 'python run.py'")
    # 在前端/请求中，我们用 List[str] 接收 target_nodes
    target_nodes: Optional[List[str]] = Field(None, description="指定指定的节点IDs")

class SpiderCreate(SpiderBase):
    pass

class SpiderUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[str] = None
    language: Optional[str] = None
    command: Optional[str] = None
    target_nodes: Optional[List[str]] = None


class SpiderFileSave(BaseModel):
    path: str = Field(..., description="ZIP 包内的文件路径")
    content: str = Field(..., description="文件内容")


class SpiderFileCreate(BaseModel):
    path: str = Field(..., description="新文件在 ZIP 包内的路径")
    content: str = Field("", description="文件初始内容，默认为空")


class SpiderFileDelete(BaseModel):
    path: str = Field(..., description="要删除的文件在 ZIP 包内的路径")


class SpiderRunRequest(BaseModel):
    target_nodes: Optional[List[str]] = Field(None, description="指定运行节点 ID 列表，为空则随机调度")
    timeout_seconds: Optional[int] = Field(None, description="任务超时秒数，默认 3600")


class SpiderOut(SpiderBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SpiderTaskOut(BaseModel):
    id: int
    task_id: str
    spider_id: int
    spider_name: str
    status: str
    node_id: Optional[str] = None
    command: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TaskLogOut(BaseModel):
    id: int
    task_id: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True
