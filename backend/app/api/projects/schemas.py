"""
项目 Pydantic Schema（请求/响应）
"""
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class ProjectBase(BaseModel):
    """项目基础属性"""
    name: str = Field(..., min_length=1, max_length=100, description="项目名称")
    description: Optional[str] = Field(None, max_length=500, description="项目描述")
    owner_id: Optional[uuid.UUID] = Field(None, description="归属用户ID")


class ProjectCreate(ProjectBase):
    """创建项目 — 仅需名称"""
    pass


class ProjectUpdate(BaseModel):
    """修改项目"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="项目名称")
    description: Optional[str] = Field(None, max_length=500, description="项目描述")


class ProjectOut(ProjectBase):
    """项目响应"""
    project_id: str
    created_at: str
    updated_at: str
    spider_count: int = 0
    is_deleted: bool = False

    class Config:
        from_attributes = True
