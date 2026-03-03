"""
爬虫项目数据库模型（SQLModel ORM）
项目是爬虫管理的顶层容器，一个项目可包含多个爬虫。
"""
from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class Project(SQLModel, table=True):
    __tablename__ = "projects"

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, unique=True, description="唯一项目 ID (proj-xxxxxxxx)")
    name: str = Field(index=True, description="项目名称")
    description: Optional[str] = Field(default=None, description="项目描述")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
