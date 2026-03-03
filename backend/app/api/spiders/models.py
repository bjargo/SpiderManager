from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime

class Spider(SQLModel, table=True):
    __tablename__ = "spiders"
    
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
    
    # 元数据
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)