from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, List

class SourceType(str, Enum):
    MINIO = "MINIO"
    GIT = "GIT"

class SpiderProjectBase(BaseModel):
    name: str = Field(..., description="项目名称或标识")
    source_type: SourceType = Field(..., description="项目代码源类型")
    source_url: str = Field(..., description="MinIO 的 Object Key 或者 Git 仓库地址")
    target_nodes: Optional[List[str]] = Field(default=None, description="指定的任务目标节点；为空代表允许所有节点执行")

class SpiderProject(SpiderProjectBase):
    project_id: str = Field(..., description="唯一项目ID")

class SpiderProjectCreate(SpiderProjectBase):
    """
    项目创建的入参 Schema
    """
    pass
