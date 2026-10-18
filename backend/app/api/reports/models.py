"""
每日任务统计日报 — 数据库模型

``report_settings`` 为单行配置表（id 固定为 1），用于在前端设置页面中
在运行时修改邮件通道与日报策略；未保存过时由环境变量提供默认值。
"""
from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field

from app.core.timezone import now

# 单行配置表的固定主键
REPORT_SETTINGS_ID = 1


class ReportSettings(SQLModel, table=True):
    """每日任务统计日报的运行时配置（单行表）"""

    __tablename__ = "report_settings"  # pyright: ignore[reportAssignmentType]

    id: Optional[int] = Field(default=REPORT_SETTINGS_ID, primary_key=True)

    # ── 日报策略 ──
    enabled: bool = Field(default=False, description="是否启用每日日报")
    hour: int = Field(default=8, description="发送时刻-小时（应用时区）")
    minute: int = Field(default=0, description="发送时刻-分钟")
    recipients: str = Field(default="", description="收件人列表，逗号/分号分隔")
    include_empty: bool = Field(default=True, description="无任务日是否仍发送简报")

    # ── SMTP 通道 ──
    smtp_host: str = Field(default="", description="SMTP 服务器地址，为空表示未配置")
    smtp_port: int = Field(default=465, description="SMTP 端口")
    smtp_user: str = Field(default="", description="SMTP 认证账号")
    smtp_password: str = Field(default="", description="SMTP 授权码/密码，接口永不回显")
    smtp_from: str = Field(default="", description="发件人地址，为空回落到 smtp_user")
    smtp_use_ssl: bool = Field(default=True, description="465 隐式 SSL")
    smtp_start_tls: bool = Field(default=False, description="587 STARTTLS（与 SSL 互斥）")
    smtp_timeout_seconds: int = Field(default=15, description="单次 SMTP 操作超时（秒）")
    smtp_max_retries: int = Field(default=3, description="发送失败重试次数")

    # ── 审计信息 ──
    updated_at: datetime = Field(default_factory=now, description="最后修改时间")
    updated_by: Optional[str] = Field(default=None, description="最后修改人邮箱")
