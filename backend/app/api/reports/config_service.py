"""
每日任务统计日报 — 运行时配置解析与持久化

配置优先级：**数据库（设置页面保存的值）> 环境变量（.env / docker-compose）**

- 从未在设置页面保存过（``report_settings`` 无数据行）时，完全沿用环境变量，行为与旧版本一致；
- 一旦保存，数据库即成为唯一事实源，避免"页面显示一套、实际发送另一套"；
- SMTP 口令例外：数据库中的口令为空时回落到环境变量 ``SMTP_PASSWORD``，
  这样运维可以把口令只留在 `.env` 里，无需在页面上重复录入。
"""
import logging
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reports.models import REPORT_SETTINGS_ID, ReportSettings
from app.core.mailer import MailConfig, parse_recipients
from app.core.timezone import now as tz_now

logger = logging.getLogger(__name__)

SOURCE_DATABASE = "database"
SOURCE_ENVIRONMENT = "environment"


@dataclass(frozen=True)
class EffectiveReportConfig:
    """解析后的生效配置（数据库与环境变量合并结果）"""

    source: str
    enabled: bool
    hour: int
    minute: int
    recipients_raw: str
    include_empty: bool
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_use_ssl: bool
    smtp_start_tls: bool
    smtp_timeout_seconds: int
    smtp_max_retries: int
    password_from_database: bool
    updated_at: str | None = None
    updated_by: str | None = None

    @property
    def recipients(self) -> list[str]:
        """解析后的收件人列表。"""
        return parse_recipients(self.recipients_raw)

    @property
    def sender(self) -> str:
        """实际发件人地址（为空时回落到认证账号）。"""
        return (self.smtp_from or self.smtp_user).strip()

    @property
    def smtp_ready(self) -> bool:
        """SMTP 通道是否具备发送条件（主机、发件人、口令齐全）。"""
        return bool(self.smtp_host and self.sender and self.smtp_password)

    @property
    def password_set(self) -> bool:
        """是否已配置口令（供接口回显，不暴露口令本身）。"""
        return bool(self.smtp_password)

    def to_mail_config(self) -> MailConfig | None:
        """
        构造邮件发送所需的连接参数。

        :return: MailConfig；未配置 SMTP 主机时返回 None
        """
        if not self.smtp_host:
            return None

        use_ssl = bool(self.smtp_use_ssl)
        return MailConfig(
            host=self.smtp_host,
            port=int(self.smtp_port),
            user=self.smtp_user,
            password=self.smtp_password,
            sender=self.sender,
            use_ssl=use_ssl,
            start_tls=bool(self.smtp_start_tls) and not use_ssl,
            timeout=int(self.smtp_timeout_seconds),
            max_retries=max(1, int(self.smtp_max_retries)),
        )


def env_config() -> EffectiveReportConfig:
    """
    从环境变量（config.settings）构造配置，作为数据库配置的兜底默认值。

    :return: source="environment" 的生效配置
    """
    from config import settings

    return EffectiveReportConfig(
        source=SOURCE_ENVIRONMENT,
        enabled=bool(settings.DAILY_REPORT_ENABLED),
        hour=int(settings.DAILY_REPORT_HOUR),
        minute=int(settings.DAILY_REPORT_MINUTE),
        recipients_raw=settings.DAILY_REPORT_RECIPIENTS or "",
        include_empty=bool(settings.DAILY_REPORT_INCLUDE_EMPTY),
        smtp_host=(settings.SMTP_HOST or "").strip(),
        smtp_port=int(settings.SMTP_PORT),
        smtp_user=(settings.SMTP_USER or "").strip(),
        smtp_password=settings.SMTP_PASSWORD or "",
        smtp_from=(settings.SMTP_FROM or "").strip(),
        smtp_use_ssl=bool(settings.SMTP_USE_SSL),
        smtp_start_tls=bool(settings.SMTP_START_TLS),
        smtp_timeout_seconds=int(settings.SMTP_TIMEOUT_SECONDS),
        smtp_max_retries=int(settings.SMTP_MAX_RETRIES),
        password_from_database=False,
    )


def row_to_config(row: ReportSettings) -> EffectiveReportConfig:
    """
    将数据库配置行转换为生效配置。

    :param row: report_settings 数据行
    :return: source="database" 的生效配置（口令为空时回落环境变量）
    """
    env = env_config()
    password = row.smtp_password or env.smtp_password

    return EffectiveReportConfig(
        source=SOURCE_DATABASE,
        enabled=bool(row.enabled),
        hour=int(row.hour),
        minute=int(row.minute),
        recipients_raw=row.recipients or "",
        include_empty=bool(row.include_empty),
        smtp_host=(row.smtp_host or "").strip(),
        smtp_port=int(row.smtp_port),
        smtp_user=(row.smtp_user or "").strip(),
        smtp_password=password,
        smtp_from=(row.smtp_from or "").strip(),
        smtp_use_ssl=bool(row.smtp_use_ssl),
        smtp_start_tls=bool(row.smtp_start_tls),
        smtp_timeout_seconds=int(row.smtp_timeout_seconds),
        smtp_max_retries=int(row.smtp_max_retries),
        password_from_database=bool(row.smtp_password),
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
        updated_by=row.updated_by,
    )


async def load_row(session: AsyncSession) -> ReportSettings | None:
    """
    读取单行配置。

    :param session: 异步数据库会话
    :return: ReportSettings；从未保存过时为 None
    """
    return await session.get(ReportSettings, REPORT_SETTINGS_ID)


async def get_effective_config(session: AsyncSession | None = None) -> EffectiveReportConfig:
    """
    获取生效的日报配置（数据库优先，未保存过则使用环境变量）。

    :param session: 可选的异步数据库会话；不传时自行创建，便于定时任务调用
    :return: EffectiveReportConfig
    """
    from app.db.database import async_session_maker

    try:
        if session is not None:
            row = await load_row(session)
            return row_to_config(row) if row else env_config()

        async with async_session_maker() as own_session:
            row = await load_row(own_session)
            return row_to_config(row) if row else env_config()
    except Exception as e:
        logger.error("读取日报配置失败，回落到环境变量: %s", e)
        return env_config()


async def save_config(
    session: AsyncSession,
    *,
    values: dict,
    operator_email: str | None,
) -> EffectiveReportConfig:
    """
    保存（新增或覆盖）日报配置。

    口令语义：``smtp_password`` 为 None 或空字符串时保持原值不变，
    避免页面上不重新输入口令就把已保存的口令清空。

    :param session: 异步数据库会话
    :param values: 待保存的字段（仅包含需要修改的键）
    :param operator_email: 操作者邮箱，用于审计
    :return: 保存后的生效配置
    """
    row = await load_row(session)
    if row is None:
        row = ReportSettings(id=REPORT_SETTINGS_ID)

    new_password = values.pop("smtp_password", None)
    for field, value in values.items():
        setattr(row, field, value)
    if new_password:
        row.smtp_password = new_password

    row.updated_at = tz_now()
    row.updated_by = operator_email

    session.add(row)
    await session.commit()
    await session.refresh(row)

    return row_to_config(row)


def describe_config(cfg: EffectiveReportConfig) -> dict:
    """
    生成可安全返回给前端的配置视图（不含口令明文）。

    :param cfg: 生效配置
    :return: 字段字典，口令以布尔标记代替
    """
    return {
        "source": cfg.source,
        "enabled": cfg.enabled,
        "hour": cfg.hour,
        "minute": cfg.minute,
        "recipients": cfg.recipients_raw,
        "recipient_list": cfg.recipients,
        "include_empty": cfg.include_empty,
        "smtp_host": cfg.smtp_host,
        "smtp_port": cfg.smtp_port,
        "smtp_user": cfg.smtp_user,
        "smtp_password_set": cfg.password_set,
        "smtp_password_from": "database" if cfg.password_from_database else ("environment" if cfg.password_set else "none"),
        "smtp_from": cfg.smtp_from,
        "smtp_use_ssl": cfg.smtp_use_ssl,
        "smtp_start_tls": cfg.smtp_start_tls,
        "smtp_timeout_seconds": cfg.smtp_timeout_seconds,
        "smtp_max_retries": cfg.smtp_max_retries,
        "smtp_ready": cfg.smtp_ready,
        "updated_at": cfg.updated_at,
        "updated_by": cfg.updated_by,
    }


def with_recipients(cfg: EffectiveReportConfig, recipients: list[str] | None) -> EffectiveReportConfig:
    """
    生成带临时收件人的配置副本（用于"发送测试邮件"等场景）。

    :param cfg: 原始生效配置
    :param recipients: 临时收件人列表；为空时返回原配置
    :return: EffectiveReportConfig
    """
    if not recipients:
        return cfg
    return replace(cfg, recipients_raw=",".join(recipients))
