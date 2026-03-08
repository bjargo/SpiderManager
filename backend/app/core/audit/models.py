"""
审计日志数据库模型（不可逆）

安全约定（强制执行于 Service 层）：
- 本表禁止任何 UPDATE / DELETE 操作，只允许 INSERT。
- 所有业务变更必须先记录审计日志，审计记录本身不可修改。
- operator_id 不设外键，防止用户删除后联动删除审计记录。
"""
import uuid
from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field

from app.core.timezone import now


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True, description="自增主键")

    # 操作者信息（快照，不做外键约束，防止用户被删后级联清除审计记录）
    operator_id: uuid.UUID = Field(
        nullable=False,
        index=True,
        description="操作者 UUID（来自 users.id，快照存储，非外键）",
    )
    role: str = Field(
        max_length=32,
        nullable=False,
        description="操作者角色快照（admin/developer/viewer）",
    )

    # 操作描述
    action: str = Field(
        max_length=64,
        nullable=False,
        index=True,
        description="操作动作（CREATE / UPDATE / DELETE / LOGIN / LOGOUT 等）",
    )
    resource_type: str = Field(
        max_length=64,
        nullable=False,
        index=True,
        description="资源类型（spider / project / user / task）",
    )
    resource_id: str = Field(
        max_length=128,
        nullable=False,
        index=True,
        description="资源 ID（字符串，跨表通用）",
    )

    # 变更值（JSON 序列化字符串，可为空，如纯查询/登录审计）
    original_value: Optional[str] = Field(
        default=None,
        description="操作前原值（JSON 序列化）",
    )
    new_value: Optional[str] = Field(
        default=None,
        description="操作后新值（JSON 序列化）",
    )

    # 请求上下文
    ip_address: Optional[str] = Field(
        default=None,
        max_length=64,
        description="请求来源 IP 地址",
    )
    status_code: int = Field(
        nullable=False,
        description="操作对应的 HTTP 响应状态码",
    )
    user_agent: Optional[str] = Field(
        default=None,
        max_length=256,
        description="User-Agent",
    )

    # 时间（只写，记录后不可修改）
    created_at: datetime = Field(
        default_factory=now,
        nullable=False,
        description="审计记录创建时间（UTC+8，只写）",
    )
