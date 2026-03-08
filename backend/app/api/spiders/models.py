import uuid
from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime

from app.core.timezone import now


class Spider(SQLModel, table=True):
    __tablename__ = "spiders"  # pyright: ignore[reportAssignmentType]

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, description="关联全局项目ID (用于拉取代码)")
    name: str = Field(index=True, description="爬虫名称")
    description: Optional[str] = Field(default=None, description="爬虫描述")

    # 代码源配置
    source_type: str = Field(description="代码源类型: MINIO 或 GIT")
    source_url: str = Field(description="MinIO Key 或 Git URL")

    # 执行配置
    language: str = Field(default="python:3.11-slim", description="Docker 基础镜像名称")
    command: Optional[str] = Field(default=None, description="执行命令，如 'python main.py'")
    target_nodes: Optional[str] = Field(default=None, description="指定执行此爬虫的节点 IDs（JSON 序列化存储）")

    # 权限归属
    owner_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="users.id",
        index=True,
        description="资源所有者 ID（关联 users.id），用于非 Admin 用户的所有权校验",
    )

    # 软删除标记
    is_deleted: bool = Field(default=False, description="软删除标记，True 表示已删除")

    # 元数据
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
