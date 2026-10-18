"""
每日任务统计日报 — APScheduler 定时任务与调度同步

包含两个 Job：

1. ``send_daily_report_job``   —— 生成并发送日报（默认统计昨天）；
2. ``sync_report_schedule``    —— **配置同步器**：周期性地把数据库中的日报配置
   （是否启用 / 发送时刻）同步到 APScheduler。

为什么需要同步器：日报配置可在前端设置页面随时修改，而 APScheduler 只在
Leader Master 节点运行；API 请求可能落在非 Leader 节点上。由 Leader 自己
周期性地对齐配置，才能保证"改完即生效"，且集群中始终只有一个节点持有该 Job。
"""
import logging
from datetime import date, timedelta

from apscheduler.triggers.cron import CronTrigger

from app.api.reports import config_service, services
from app.api.reports.config_service import EffectiveReportConfig
from app.core.mailer import MailError, MailNotConfigured, send_email
from app.core.timezone import now as tz_now

logger = logging.getLogger(__name__)

# 日报 Job 在 APScheduler 中的固定 ID（供注册、改点、移除与状态查询复用）
DAILY_REPORT_JOB_ID = "daily_task_report"


async def send_daily_report_job(report_date: date | None = None) -> None:
    """
    生成并发送指定日期的任务统计日报。

    :param report_date: 报告归属日期；默认统计「昨天」（应用时区）
    :return: None（发送失败只记录日志，不向上抛出，避免影响其它定时任务）
    """
    cfg = await config_service.get_effective_config()

    if not cfg.enabled:
        logger.info("每日日报未启用，跳过本次发送")
        return

    target_date = report_date or (tz_now().date() - timedelta(days=1))

    recipients = cfg.recipients
    if not recipients:
        logger.warning("每日日报未配置收件人，跳过 %s 的发送", target_date)
        return

    mail_config = cfg.to_mail_config()
    if mail_config is None:
        logger.warning("每日日报未配置 SMTP 主机，跳过 %s 的发送", target_date)
        return

    try:
        report = await services.collect_daily_report(target_date)

        if report.total_tasks == 0 and not cfg.include_empty:
            logger.info("每日日报：%s 无任务且未开启空日报发送，跳过", target_date)
            return

        subject, html_body, text_body = services.render_report(report)
        sent_to = await send_email(subject, html_body, text_body, recipients, config=mail_config)
        logger.info(
            "每日日报发送完成: date=%s tasks=%d success=%d failed=%d records=%d recipients=%s",
            target_date, report.total_tasks, report.success_count,
            report.failed_count, report.total_records, ", ".join(sent_to),
        )
    except MailNotConfigured as e:
        logger.warning("每日日报跳过发送: %s", e)
    except MailError as e:
        logger.error("每日日报发送失败: %s", e)
    except Exception as e:
        logger.error("每日日报生成或发送出现异常: %s", e, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# 调度同步
# ─────────────────────────────────────────────────────────────────────────────

def _cron_matches(trigger: object, hour: int, minute: int) -> bool:
    """
    判断现有触发器的小时/分钟是否与目标一致。

    :param trigger: APScheduler 触发器对象
    :param hour: 目标小时
    :param minute: 目标分钟
    :return: 一致返回 True；无法识别时返回 False（按需要改点处理）
    """
    if not isinstance(trigger, CronTrigger):
        return False

    try:
        fields = {field.name: str(field) for field in trigger.fields}
    except Exception:
        return False

    return fields.get("hour") == str(hour) and fields.get("minute") == str(minute)


def _describe_next_run(job: object) -> str | None:
    """提取 Job 的下次运行时间文本。"""
    next_run = getattr(job, "next_run_time", None)
    return str(next_run) if next_run else None


async def sync_report_schedule() -> None:
    """
    将数据库中的日报配置同步到 APScheduler。

    行为：
    - 未启用 → 移除已注册的日报 Job（若存在）；
    - 已启用且未注册 → 按配置时刻注册；
    - 已启用但时刻变化 → 就地改点（reschedule），不中断其它 Job。

    :return: None（任何异常都只记录日志，保证调度器本身稳定）
    """
    from app.core.scheduler import scheduler

    try:
        cfg = await config_service.get_effective_config()
    except Exception as e:
        logger.error("读取日报配置失败，跳过本次调度同步: %s", e)
        return

    try:
        job = scheduler.get_job(DAILY_REPORT_JOB_ID)

        if not cfg.enabled:
            if job is not None:
                scheduler.remove_job(DAILY_REPORT_JOB_ID)
                logger.info("日报已关闭，已移除调度任务 '%s'", DAILY_REPORT_JOB_ID)
            return

        if job is None:
            scheduler.add_job(
                send_daily_report_job,
                trigger="cron",
                hour=cfg.hour,
                minute=cfg.minute,
                id=DAILY_REPORT_JOB_ID,
                replace_existing=True,
                misfire_grace_time=3600,
            )
            logger.info(
                "已注册日报任务：每日 %02d:%02d（配置来源: %s）",
                cfg.hour, cfg.minute, cfg.source,
            )
            return

        if not _cron_matches(job.trigger, cfg.hour, cfg.minute):
            scheduler.reschedule_job(
                DAILY_REPORT_JOB_ID,
                trigger=CronTrigger(hour=cfg.hour, minute=cfg.minute),
            )
            logger.info("日报发送时间已更新为每日 %02d:%02d", cfg.hour, cfg.minute)
    except Exception as e:
        logger.error("同步日报调度失败: %s", e, exc_info=True)


async def apply_schedule_now() -> None:
    """
    在前端保存配置后立即尝试生效（仅当本节点正在运行调度器，即 Leader）。

    非 Leader 节点直接返回，由 Leader 的周期同步器在 1 分钟内自动对齐。

    :return: None
    """
    from app.core.scheduler import scheduler

    if not scheduler.running:
        logger.info("本节点未运行调度器（非 Leader），日报配置将由 Leader 自动同步")
        return

    await sync_report_schedule()


def get_schedule_status() -> dict:
    """
    查询本节点调度器中日报任务的注册状态（供设置页面回显）。

    :return: {"scheduler_active": bool, "job_registered": bool, "next_run_time": str | None}
    """
    from app.core.scheduler import scheduler

    running = bool(scheduler.running)
    job = scheduler.get_job(DAILY_REPORT_JOB_ID) if running else None

    return {
        "scheduler_active": running,
        "job_registered": job is not None,
        "next_run_time": _describe_next_run(job) if job is not None else None,
    }


def schedule_hint(cfg: EffectiveReportConfig) -> str:
    """生成人类可读的调度说明，便于前端提示用户。"""
    if not cfg.enabled:
        return "日报已停用，不会自动发送"
    if not cfg.smtp_ready:
        return "已启用，但 SMTP 通道不完整（服务器/发件人/口令），到期将跳过发送"
    if not cfg.recipients:
        return "已启用，但未配置收件人，到期将跳过发送"
    return f"每日 {cfg.hour:02d}:{cfg.minute:02d} 自动发送前一日日报"
