"""
用户数据库模型
使用 SQLModel 定义表结构（与其他模型共享同一 metadata，确保外键可正确解析），
数据库适配器使用 fastapi-users-db-sqlalchemy（兼容 SQLModel + SQLAlchemy 模型）。

包含字段：id (UUID), email, hashed_password, is_active, is_superuser, is_verified
扩展字段：role（用户角色）
"""
import uuid
from typing import Optional

from sqlmodel import SQLModel, Field, Column
from sqlalchemy import Enum as SAEnum

from app.core.enums import UserRole


class User(SQLModel, table=True):
    """用户表，与 SQLModel 共享 metadata，确保跨表外键可被正确解析。"""
    __tablename__ = "users"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        nullable=False,
    )
    email: str = Field(
        unique=True,
        index=True,
        max_length=320,
        nullable=False,
    )
    hashed_password: str = Field(nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    is_superuser: bool = Field(default=False, nullable=False)
    is_verified: bool = Field(default=False, nullable=False)
    role: Optional[UserRole] = Field(
        default=UserRole.developer,
        sa_column=Column(SAEnum(UserRole, name="userrole"), nullable=False),
    )
