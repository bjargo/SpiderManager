"""
项目 Pydantic Schema（请求/响应）
"""
from typing import Optional
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """创建项目 — 仅需名称"""
    name: str = Field(..., min_length=1, max_length=100, description="项目名称")
    description: Optional[str] = Field(None, max_length=500, description="项目描述")


class ProjectUpdate(BaseModel):
    """修改项目"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="项目名称")
    description: Optional[str] = Field(None, max_length=500, description="项目描述")


class ProjectOut(BaseModel):
    """项目响应"""
    project_id: str
    name: str
    description: Optional[str] = None
    created_at: str
    updated_at: str
    spider_count: int = 0

    class Config:
        from_attributes = True
