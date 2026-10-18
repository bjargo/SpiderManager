"""
每日任务统计日报 — 接口层

日报数据接口（均需 admin 权限）：
  GET  /api/reports/daily/preview       — 预览指定日期的日报数据（JSON）
  GET  /api/reports/daily/preview-html  — 预览日报邮件的 HTML 效果（浏览器直接查看）
  POST /api/reports/daily/send          — 立即生成并发送日报（支持临时收件人，便于联调）

配置接口（均需 admin 权限）：
  GET  /api/reports/settings            — 读取生效配置（数据库优先，回落环境变量）
  POST /api/reports/settings            — 保存配置（保存后立即尝试对齐调度）
  POST /api/reports/settings/test-mail  — 使用当前配置发送测试邮件
"""
import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.reports import config_service, services
from app.api.reports.config_service import EffectiveReportConfig
from app.api.reports.schemas import (
    DailyReport,
    ReportSettingsOut,
    ReportSettingsUpdate,
    SendDailyReportRequest,
    SendDailyReportResponse,
    TestMailRequest,
    TestMailResponse,
)
from app.api.users.models import User
from app.core.dependencies import require_admin
from app.core.mailer import (
    MailError,
    MailNotConfigured,
    is_valid_address,
    parse_recipients,
    send_email,
)
from app.core.schemas.api_response import ApiResponse
from app.core.timezone import now as tz_now
from app.db.database import get_async_session
from app.worker import report_jobs

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_report_date(value: Optional[date]) -> date:
    """未指定日期时默认统计「昨天」（与定时任务口径一致）。"""
    return value or (tz_now().date() - timedelta(days=1))


def _validate_recipients(recipients: list[str]) -> list[str]:
    """
    校验并规范化收件人列表。

    :param recipients: 待校验的收件人列表
    :return: 去重后的收件人列表
    :raises HTTPException: 400 — 存在格式非法的邮箱地址
    """
    cleaned = parse_recipients(",".join(recipients))
    invalid = [addr for addr in cleaned if not is_valid_address(addr)]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"以下邮箱地址格式无效: {', '.join(invalid)}",
        )
    return cleaned


def _build_settings_view(cfg: EffectiveReportConfig) -> ReportSettingsOut:
    """
    组装配置视图（合并生效配置、调度状态与提示文案）。

    :param cfg: 生效配置
    :return: ReportSettingsOut（不含口令明文）
    """
    return ReportSettingsOut(
        **config_service.describe_config(cfg),
        **report_jobs.get_schedule_status(),
        schedule_hint=report_jobs.schedule_hint(cfg),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 日报数据
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/daily/preview",
    response_model=ApiResponse[DailyReport],
    summary="预览每日任务日报数据",
    description=(
        "按指定日期生成日报统计数据（不发送邮件）。默认为昨天。\n"
        "统计窗口为该日 00:00:00 ~ 次日 00:00:00（应用时区），"
        "采集数据量取自爬虫数据表中已落库的记录数。"
    ),
)
async def preview_daily_report(
    date_: Optional[date] = Query(default=None, alias="date", description="报告归属日期（YYYY-MM-DD），默认昨天"),
    _: User = Depends(require_admin),
) -> ApiResponse[DailyReport]:
    """
    生成并返回日报统计数据。

    :param date_: 报告归属日期，默认昨天
    :param _: 注入的当前操作者（需 admin 角色）
    :return: ApiResponse 包含 DailyReport
    """
    report_date = _resolve_report_date(date_)
    report = await services.collect_daily_report(report_date)
    return ApiResponse.success(data=report)


@router.get(
    "/daily/preview-html",
    response_class=HTMLResponse,
    summary="预览日报邮件 HTML 效果",
    description="返回与真实邮件完全一致的 HTML 内容，便于在浏览器中直接确认排版与配色。",
)
async def preview_daily_report_html(
    date_: Optional[date] = Query(default=None, alias="date", description="报告归属日期（YYYY-MM-DD），默认昨天"),
    _: User = Depends(require_admin),
) -> HTMLResponse:
    """
    渲染并返回日报的 HTML 正文。

    :param date_: 报告归属日期，默认昨天
    :param _: 注入的当前操作者（需 admin 角色）
    :return: HTMLResponse（邮件同一份模板渲染结果）
    """
    report_date = _resolve_report_date(date_)
    report = await services.collect_daily_report(report_date)
    _, html_body, _ = services.render_report(report)
    return HTMLResponse(content=html_body)


@router.post(
    "/daily/send",
    response_model=ApiResponse[SendDailyReportResponse],
    summary="立即发送每日任务日报",
    description=(
        "立即生成指定日期的日报并发送邮件，可用于功能联调、漏发补发。\n"
        "请求体可携带 recipients 临时收件人列表（仅本次生效）；"
        "不传时使用设置页面/环境变量中配置的收件人。"
    ),
)
async def send_daily_report(
    body: SendDailyReportRequest,
    _: User = Depends(require_admin),
) -> ApiResponse[SendDailyReportResponse]:
    """
    手动触发日报发送。

    :param body: 请求体（date 指定报告日期，recipients 指定临时收件人）
    :param _: 注入的当前操作者（需 admin 角色）
    :return: ApiResponse 包含发送结果摘要
    :raises HTTPException: 400 — 收件人缺失或格式非法；503 — SMTP 未配置或发送失败
    """
    report_date = _resolve_report_date(body.date)
    recipients = _validate_recipients(body.recipients) if body.recipients else None

    cfg = await config_service.get_effective_config()
    if recipients is None:
        recipients = cfg.recipients
        if not recipients:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="未配置收件人，请在「系统设置」中填写收件人，或在请求中临时指定",
            )

    mail_config = cfg.to_mail_config()
    if mail_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SMTP 未配置（缺少服务器地址），请在「系统设置」中完成邮件通道配置",
        )

    report = await services.collect_daily_report(report_date)
    subject, html_body, text_body = services.render_report(report)

    try:
        sent_to = await send_email(subject, html_body, text_body, recipients, config=mail_config)
    except MailNotConfigured as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except MailError as e:
        logger.error("日报邮件发送失败: %s", e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    return ApiResponse.success(
        data=SendDailyReportResponse(
            report_date=report.report_date,
            recipients=sent_to,
            total_tasks=report.total_tasks,
            success_count=report.success_count,
            failed_count=report.failed_count,
            total_records=report.total_records,
        ),
        message=f"日报已发送至 {len(sent_to)} 个收件人",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 配置读写
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/settings",
    response_model=ApiResponse[ReportSettingsOut],
    summary="读取日报与邮件配置",
    description=(
        "返回当前生效的日报配置。配置优先级：设置页面保存的值 > 环境变量。\n"
        "SMTP 口令永不回显，仅返回是否已配置及其来源。"
    ),
)
async def get_report_settings(
    session: AsyncSession = Depends(get_async_session),
    _: User = Depends(require_admin),
) -> ApiResponse[ReportSettingsOut]:
    """
    读取生效配置与调度状态。

    :param session: 注入的数据库会话
    :param _: 注入的当前操作者（需 admin 角色）
    :return: ApiResponse 包含 ReportSettingsOut
    """
    cfg = await config_service.get_effective_config(session)
    return ApiResponse.success(data=_build_settings_view(cfg))


@router.post(
    "/settings",
    response_model=ApiResponse[ReportSettingsOut],
    summary="保存日报与邮件配置",
    description=(
        "保存后数据库立即成为唯一事实源，并尝试立刻对齐调度"
        "（非 Leader 节点由 Leader 在 1 分钟内自动同步）。\n"
        "smtp_password 留空表示保持原口令不变。"
    ),
)
async def update_report_settings(
    body: ReportSettingsUpdate,
    session: AsyncSession = Depends(get_async_session),
    operator: User = Depends(require_admin),
) -> ApiResponse[ReportSettingsOut]:
    """
    保存日报配置。

    :param body: 配置请求体
    :param session: 注入的数据库会话
    :param operator: 注入的当前操作者（需 admin 角色）
    :return: ApiResponse 包含保存后的配置视图
    :raises HTTPException: 400 — 收件人格式非法或启用时缺少必要字段
    """
    values = body.model_dump()

    recipient_list = _validate_recipients(parse_recipients(values.get("recipients") or ""))
    values["recipients"] = ",".join(recipient_list)

    existing = await config_service.get_effective_config(session)
    password_after_save = (values.get("smtp_password") or "").strip() or existing.smtp_password

    if values.get("enabled"):
        missing: list[str] = []
        if not (values.get("smtp_host") or "").strip():
            missing.append("SMTP 服务器地址")
        if not ((values.get("smtp_from") or "").strip() or (values.get("smtp_user") or "").strip()):
            missing.append("发件人地址（或认证账号）")
        if not password_after_save:
            missing.append("SMTP 授权码")
        if not recipient_list:
            missing.append("收件人")
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"启用日报前请先完善以下配置: {', '.join(missing)}",
            )

    cfg = await config_service.save_config(
        session,
        values=values,
        operator_email=getattr(operator, "email", None),
    )
    await report_jobs.apply_schedule_now()

    return ApiResponse.success(
        data=_build_settings_view(cfg),
        message="配置已保存",
    )


@router.post(
    "/settings/test-mail",
    response_model=ApiResponse[TestMailResponse],
    summary="发送测试邮件",
    description=(
        "使用当前已保存的配置立即投递一封测试邮件，用于验证服务器地址、"
        "端口、加密方式与授权码是否正确。可临时指定收件人，不改变已保存的配置。"
    ),
)
async def send_test_mail(
    body: TestMailRequest,
    _: User = Depends(require_admin),
) -> ApiResponse[TestMailResponse]:
    """
    使用当前配置发送测试邮件。

    :param body: 请求体（可临时指定收件人）
    :param _: 注入的当前操作者（需 admin 角色）
    :return: ApiResponse 包含实际收件人
    :raises HTTPException: 400 — 收件人缺失或格式非法；503 — SMTP 未配置或发送失败
    """
    cfg = await config_service.get_effective_config()

    recipients = _validate_recipients(body.recipients) if body.recipients else cfg.recipients
    if not recipients:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未指定收件人，请先配置收件人或临时传入 recipients",
        )

    mail_config = cfg.to_mail_config()
    if mail_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SMTP 未配置（缺少服务器地址），请先保存邮件通道配置",
        )
    if not mail_config.sender or not mail_config.password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SMTP 通道不完整（缺少发件人或授权码），请先完善配置",
        )

    sent_at = tz_now().strftime("%Y-%m-%d %H:%M:%S")
    encryption = "SSL" if mail_config.use_ssl else ("STARTTLS" if mail_config.start_tls else "明文")
    subject = f"[SpiderManage] 邮件通道测试（{sent_at}）"
    html_body = (
        "<div style=\"font-family:-apple-system,'Microsoft YaHei',Arial,sans-serif;color:#1f2329;\">"
        "<h3 style=\"margin:0 0 10px 0;\">SpiderManage 邮件通道测试</h3>"
        "<p style=\"margin:0 0 6px 0;\">收到这封邮件，说明日报邮件的发送通道配置正确。</p>"
        f"<p style=\"margin:0;color:#86909c;font-size:13px;\">发送时间：{sent_at}<br>"
        f"SMTP 服务器：{mail_config.host}:{mail_config.port}（{encryption}）</p>"
        "</div>"
    )
    text_body = (
        "SpiderManage 邮件通道测试\n"
        "收到这封邮件，说明日报邮件的发送通道配置正确。\n"
        f"发送时间：{sent_at}\n"
        f"SMTP 服务器：{mail_config.host}:{mail_config.port}（{encryption}）\n"
    )

    try:
        sent_to = await send_email(subject, html_body, text_body, recipients, config=mail_config)
    except MailNotConfigured as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except MailError as e:
        logger.error("测试邮件发送失败: %s", e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    return ApiResponse.success(
        data=TestMailResponse(recipients=sent_to),
        message=f"测试邮件已发送至 {len(sent_to)} 个收件人",
    )
