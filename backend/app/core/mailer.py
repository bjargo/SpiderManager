"""
邮件发送模块（基于标准库 smtplib，零新增依赖）

设计要点：
1. 全异步接口 —— 内部通过 ``asyncio.to_thread`` 调用阻塞的 smtplib，
   避免邮件投递阻塞 Master 节点事件循环；
2. 双加密模式 —— 支持隐式 SSL（465，QQ/163 等推荐）与 STARTTLS（587）；
3. 失败可重试 —— 默认 3 次指数退避，异常统一包装为 ``MailError``；
4. 配置缺失 —— 抛出 ``MailNotConfigured``，由调用方降级为「跳过发送」。

安全约定：
SMTP 密码（通常为邮箱授权码）只从配置读取，任何日志中都不输出该值。
"""
import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from config import settings

logger = logging.getLogger(__name__)


class MailError(Exception):
    """邮件发送失败（连接、认证、投递阶段的所有异常统一包装）。"""


class MailNotConfigured(MailError):
    """SMTP 或收件人未配置，调用方应跳过本次发送而不是重试。"""


@dataclass(frozen=True)
class MailConfig:
    """一次邮件投递所需的 SMTP 连接参数。"""

    host: str
    port: int
    user: str
    password: str
    sender: str
    use_ssl: bool
    start_tls: bool
    timeout: int
    max_retries: int

    @classmethod
    def from_settings(cls) -> "MailConfig | None":
        """
        从全局配置构建连接参数。

        :return: MailConfig 实例；SMTP_HOST 为空时返回 None（表示未启用邮件能力）
        """
        host = (settings.SMTP_HOST or "").strip()
        if not host:
            return None

        use_ssl = bool(settings.SMTP_USE_SSL)
        return cls(
            host=host,
            port=int(settings.SMTP_PORT),
            user=(settings.SMTP_USER or "").strip(),
            password=settings.SMTP_PASSWORD or "",
            sender=(settings.SMTP_FROM or settings.SMTP_USER or "").strip(),
            use_ssl=use_ssl,
            start_tls=bool(settings.SMTP_START_TLS) and not use_ssl,
            timeout=int(settings.SMTP_TIMEOUT_SECONDS),
            max_retries=max(1, int(settings.SMTP_MAX_RETRIES)),
        )


def parse_recipients(raw: str | None) -> list[str]:
    """
    解析收件人字符串为邮箱列表。

    支持英文逗号、分号、换行混排，自动去重并保持书写顺序。

    :param raw: 形如 "a@x.com, b@y.com;c@z.com" 的原始字符串
    :return: 去重后的收件人列表
    """
    if not raw:
        return []

    normalized = raw.replace(";", ",").replace("\n", ",").replace("\r", ",")
    seen: set[str] = set()
    recipients: list[str] = []
    for item in normalized.split(","):
        addr = item.strip()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        recipients.append(addr)
    return recipients


def is_valid_address(address: str) -> bool:
    """邮箱地址的轻量校验（拦截明显错误，最终仍以 SMTP 服务器判断为准）。"""
    addr = (address or "").strip()
    if not addr or " " in addr or "@" not in addr:
        return False
    local, _, domain = addr.rpartition("@")
    return bool(local) and bool(domain) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def _build_message(
    cfg: MailConfig,
    subject: str,
    html_body: str | None,
    text_body: str | None,
    recipients: list[str],
) -> EmailMessage:
    """构建多部件邮件（纯文本兜底 + 可选 HTML 正文），中文主题由标准库自动 RFC 2047 编码。"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg.sender
    message["To"] = ", ".join(recipients)
    message.set_content(text_body or "")
    if html_body:
        message.add_alternative(html_body, subtype="html")
    return message


def _send_sync(
    cfg: MailConfig,
    subject: str,
    html_body: str | None,
    text_body: str | None,
    recipients: list[str],
) -> None:
    """在阻塞线程中完成一次投递：连接 → 加密 → 认证 → 发送 → 退出。"""
    context = ssl.create_default_context()
    if cfg.use_ssl:
        client: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout, context=context)
    else:
        client = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)

    try:
        if cfg.start_tls:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if cfg.user:
            client.login(cfg.user, cfg.password)
        client.send_message(_build_message(cfg, subject, html_body, text_body, recipients))
    finally:
        try:
            client.quit()
        except Exception:  # 连接可能已断开，退出失败不影响投递结果判定
            client.close()


async def send_email(
    subject: str,
    html_body: str | None = None,
    text_body: str | None = None,
    recipients: list[str] | None = None,
    config: MailConfig | None = None,
) -> list[str]:
    """
    异步发送一封邮件。

    :param subject: 邮件主题（中文自动按 RFC 2047 编码）
    :param html_body: HTML 正文（可选）
    :param text_body: 纯文本正文（可选，作为不支持 HTML 客户端的兜底）
    :param recipients: 收件人列表；为 None 时回落到配置 DAILY_REPORT_RECIPIENTS
    :param config: SMTP 连接参数；为 None 时从全局配置构建
    :return: 实际使用的收件人列表
    :raises MailNotConfigured: SMTP 主机、发件人或收件人未配置
    :raises MailError: 重试耗尽后仍失败
    """
    cfg = config or MailConfig.from_settings()
    if cfg is None:
        raise MailNotConfigured("SMTP 未配置（SMTP_HOST 为空），跳过邮件发送")
    if not cfg.sender:
        raise MailNotConfigured("发件人未配置（SMTP_FROM / SMTP_USER 均为空）")

    if recipients is None:
        to_list = parse_recipients(settings.DAILY_REPORT_RECIPIENTS)
    else:
        to_list = [addr.strip() for addr in recipients if addr and addr.strip()]
    if not to_list:
        raise MailNotConfigured("收件人列表为空，跳过邮件发送")

    last_error: Exception | None = None
    for attempt in range(1, cfg.max_retries + 1):
        try:
            await asyncio.to_thread(_send_sync, cfg, subject, html_body, text_body, to_list)
            logger.info("邮件发送成功: subject=%s recipients=%s", subject, ", ".join(to_list))
            return to_list
        except Exception as e:  # 统一转译为 MailError，避免向上层泄漏底层异常类型
            last_error = e
            logger.warning("邮件发送失败（第 %d/%d 次）: %s", attempt, cfg.max_retries, e)
            if attempt < cfg.max_retries:
                await asyncio.sleep(2 ** (attempt - 1))

    raise MailError(f"邮件发送失败（已重试 {cfg.max_retries} 次）: {last_error}") from last_error
