"""
每日任务统计日报 — 回归测试

覆盖范围：
1. 统计窗口计算与任务状态归类（纯函数）；
2. 汇总逻辑：成功/失败/取消/未完成计数、成功率、采集量、按爬虫聚合排序；
3. 邮件渲染：主题、HTML/纯文本正文、HTML 转义（防注入）、空日报提示；
4. 邮件发送：收件人解析与校验、重试、未配置时的降级；
5. 定时任务：配置关闭 / 缺收件人 / 缺 SMTP 时跳过，正常路径完整发送；
6. 调度器接线与配置同步器：注册、改点、移除。

说明：环境未安装 pytest-asyncio，故异步函数统一用 asyncio.run 在同步测试内驱动。
"""
import asyncio
import os
import sys
from datetime import date, datetime
from unittest.mock import patch

from apscheduler.triggers.cron import CronTrigger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.core.mailer as mailer
import app.worker.report_jobs as report_jobs
from app.api.reports import config_service, services
from app.api.reports.schemas import ReportTaskRow
from config import settings

from dataclasses import replace as dataclasses_replace


# ─────────────────────────────────────────────────────────────────────────────
# 测试辅助
# ─────────────────────────────────────────────────────────────────────────────

REPORT_DATE = date(2026, 3, 5)


def _row(
    task_id: str,
    spider_name: str,
    status: str,
    *,
    created_hour: int = 10,
    duration: float | None = 120.0,
    node_id: str = "node-1",
    error_detail: str | None = None,
) -> ReportTaskRow:
    """构造一条任务明细行，默认位于报告日当天。"""
    created_at = datetime(REPORT_DATE.year, REPORT_DATE.month, REPORT_DATE.day, created_hour, 0, 0)
    started_at = created_at if duration is not None else None
    finished_at = created_at.replace(hour=created_hour + 1) if duration is not None else None
    return ReportTaskRow(
        task_id=task_id,
        spider_id=1,
        spider_name=spider_name,
        status=status,
        bucket=services.classify_status(status),
        node_id=node_id,
        created_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration,
        error_detail=error_detail,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. 纯函数
# ─────────────────────────────────────────────────────────────────────────────

def test_build_window_covers_whole_day():
    start, end = services.build_window(REPORT_DATE)
    assert start == datetime(2026, 3, 5, 0, 0, 0)
    assert end == datetime(2026, 3, 6, 0, 0, 0)
    assert (end - start).total_seconds() == 86400


def test_classify_status_maps_all_known_states():
    assert services.classify_status("success") == "success"
    for status_value in ("failed", "timeout", "error"):
        assert services.classify_status(status_value) == "failed"
    assert services.classify_status("cancelled") == "cancelled"
    assert services.classify_status("canceled") == "cancelled"
    for status_value in ("pending", "running"):
        assert services.classify_status(status_value) == "pending"


def test_classify_status_normalizes_case_and_whitespace():
    assert services.classify_status("  SUCCESS ") == "success"


def test_classify_status_unknown_falls_back_to_pending():
    assert services.classify_status("weird-state") == "pending"
    assert services.classify_status("") == "pending"


def test_humanize_duration_formats_units():
    assert services.humanize_duration(None) == "-"
    assert services.humanize_duration(0) == "0 秒"
    assert services.humanize_duration(45.4) == "45 秒"
    assert services.humanize_duration(125) == "2 分 5 秒"
    assert services.humanize_duration(3725) == "1 小时 2 分"


def test_format_int_uses_thousands_separator():
    assert services.format_int(0) == "0"
    assert services.format_int(12500) == "12,500"
    assert services.format_int(None) == "0"


def test_truncate_error_limits_length():
    short = services._truncate_error("boom")
    assert short == "boom"

    long_detail = "x" * (services.ERROR_DETAIL_MAX_LEN + 50)
    truncated = services._truncate_error(long_detail)
    assert truncated.endswith("（已截断）")
    assert len(truncated) < len(long_detail)

    assert services._truncate_error(None) is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. 汇总逻辑
# ─────────────────────────────────────────────────────────────────────────────

def test_summarize_computes_counts_rate_and_records():
    rows = [
        _row("t-1", "news_spider", "success", created_hour=1, duration=60.0),
        _row("t-2", "news_spider", "success", created_hour=2, duration=120.0),
        _row("t-3", "products_spider", "success", created_hour=3, duration=180.0),
        _row("t-4", "comments_spider", "failed", created_hour=4, duration=30.0, error_detail="exit code: 1"),
        _row("t-5", "comments_spider", "timeout", created_hour=5, duration=None),
        _row("t-6", "news_spider", "cancelled", created_hour=6, duration=10.0),
        _row("t-7", "news_spider", "running", created_hour=7, duration=None),
    ]
    records = {"t-1": 100, "t-2": 250, "t-3": 1000}

    report = services.summarize(rows, REPORT_DATE, records_by_task=records)

    assert report.total_tasks == 7
    assert report.success_count == 3
    assert report.failed_count == 2
    assert report.cancelled_count == 1
    assert report.pending_count == 1
    # 成功率分母只含 success + failed
    assert report.success_rate == round(3 / 5, 4)
    # 采集量只累计成功任务的已落库条数
    assert report.total_records == 1350
    # 平均耗时基于有 started_at/finished_at 的任务（6 条，t-5/t-7 无耗时）
    assert report.avg_duration_seconds == round((60 + 120 + 180 + 30 + 10) / 5, 2)
    assert report.report_date == REPORT_DATE
    assert report.window_start == datetime(2026, 3, 5, 0, 0, 0)
    assert report.window_end == datetime(2026, 3, 6, 0, 0, 0)
    assert report.records_scope_note == services.RECORDS_SCOPE_NOTE


def test_summarize_rollup_groups_by_spider_sorted_by_records():
    rows = [
        _row("t-1", "news_spider", "success", created_hour=1, duration=60.0),
        _row("t-2", "news_spider", "success", created_hour=2, duration=120.0),
        _row("t-3", "products_spider", "success", created_hour=3, duration=180.0),
    ]
    report = services.summarize(rows, REPORT_DATE, records_by_task={"t-1": 10, "t-2": 20, "t-3": 999})

    assert [s.spider_name for s in report.success_spiders] == ["products_spider", "news_spider"]
    news = next(s for s in report.success_spiders if s.spider_name == "news_spider")
    assert news.runs == 2
    assert news.records == 30
    assert news.duration_seconds == 180.0
    assert sorted(news.task_ids) == ["t-1", "t-2"]


def test_summarize_partial_flag_and_skipped_tables():
    report = services.summarize(
        [_row("t-1", "news_spider", "success")],
        REPORT_DATE,
        records_by_task={"t-1": 5},
        records_partial=True,
        records_skipped_tables=["big_table"],
    )
    assert report.records_partial is True
    assert report.records_skipped_tables == ["big_table"]


def test_summarize_empty_day_has_zero_rate():
    report = services.summarize([], REPORT_DATE)
    assert report.total_tasks == 0
    assert report.success_rate == 0.0
    assert report.avg_duration_seconds is None
    assert report.success_spiders == []


def test_summarize_orders_task_lists_by_created_at():
    rows = [
        _row("t-late", "a", "failed", created_hour=20),
        _row("t-early", "a", "failed", created_hour=1),
    ]
    report = services.summarize(rows, REPORT_DATE)
    assert [t.task_id for t in report.failed_tasks] == ["t-early", "t-late"]


def test_fetch_records_by_task_short_circuits_on_empty_input():
    counts, partial, skipped = asyncio.run(services.fetch_records_by_task([]))
    assert counts == {}
    assert partial is False
    assert skipped == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. 邮件渲染
# ─────────────────────────────────────────────────────────────────────────────

def _sample_report():
    rows = [
        _row("t-1", "news_spider", "success", created_hour=1, duration=60.0),
        _row("t-2", "comments_spider", "failed", created_hour=2, duration=30.0, error_detail="exit code: 1"),
    ]
    return services.summarize(rows, REPORT_DATE, records_by_task={"t-1": 1234}, generated_at=datetime(2026, 3, 6, 8, 0, 0))


def test_build_subject_contains_key_metrics():
    subject = services.build_subject(_sample_report())
    assert "2026-03-05" in subject
    assert "成功 1" in subject
    assert "失败 1" in subject
    assert "1,234" in subject


def test_build_subject_for_empty_day():
    subject = services.build_subject(services.summarize([], REPORT_DATE))
    assert "2026-03-05" in subject
    assert "当日无任务" in subject


def test_render_report_returns_subject_html_and_text():
    subject, html_body, text_body = services.render_report(_sample_report())

    assert "2026-03-05" in subject
    assert "<html" in html_body.lower()
    assert "news_spider" in html_body
    assert "comments_spider" in html_body
    assert "1,234" in html_body
    # 纯文本兜底必须包含核心信息，且不含 HTML 标签
    assert "news_spider" in text_body
    assert "1,234" in text_body
    assert "<table" not in text_body


def test_render_report_html_escapes_user_content():
    rows = [
        _row("t-1", "<script>alert(1)</script>", "success"),
        _row("t-2", "safe_spider", "failed", error_detail="<img src=x onerror=alert(2)>"),
    ]
    report = services.summarize(rows, REPORT_DATE, records_by_task={"t-1": 1})
    _, html_body, _ = services.render_report(report)

    assert "<script>alert(1)</script>" not in html_body
    assert "&lt;script&gt;" in html_body
    assert "<img src=x onerror=alert(2)>" not in html_body
    assert "&lt;img" in html_body


def test_render_report_marks_partial_records():
    report = services.summarize(
        [_row("t-1", "news_spider", "success")],
        REPORT_DATE,
        records_by_task={"t-1": 1},
        records_partial=True,
        records_skipped_tables=["tbl_a", "tbl_b"],
    )
    _, html_body, text_body = services.render_report(report)
    assert "tbl_a" in html_body and "tbl_b" in html_body
    assert "tbl_a" in text_body


def test_render_report_handles_empty_day():
    _, html_body, text_body = services.render_report(services.summarize([], REPORT_DATE))
    assert "当日没有运行成功的爬虫" in html_body
    assert "当日没有运行成功的爬虫" in text_body


# ─────────────────────────────────────────────────────────────────────────────
# 4. 邮件发送
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_recipients_supports_multiple_separators_and_dedupes():
    parsed = mailer.parse_recipients("a@x.com, b@y.com;c@z.com\nd@w.com, a@x.com")
    assert parsed == ["a@x.com", "b@y.com", "c@z.com", "d@w.com"]
    assert mailer.parse_recipients(None) == []
    assert mailer.parse_recipients("   ") == []


def test_is_valid_address_rejects_obvious_mistakes():
    assert mailer.is_valid_address("ops@example.com") is True
    assert mailer.is_valid_address("no-at-sign.com") is False
    assert mailer.is_valid_address("a b@example.com") is False
    assert mailer.is_valid_address("@example.com") is False
    assert mailer.is_valid_address("user@localhost") is False
    assert mailer.is_valid_address("") is False


def test_mail_config_returns_none_without_host():
    with patch.object(settings, "SMTP_HOST", ""):
        assert mailer.MailConfig.from_settings() is None


def test_mail_config_from_settings_prefers_ssl_and_falls_back_sender():
    with patch.object(settings, "SMTP_HOST", "smtp.qq.com"), \
         patch.object(settings, "SMTP_PORT", 465), \
         patch.object(settings, "SMTP_USER", "bot@qq.com"), \
         patch.object(settings, "SMTP_PASSWORD", "authcode"), \
         patch.object(settings, "SMTP_FROM", ""), \
         patch.object(settings, "SMTP_USE_SSL", True), \
         patch.object(settings, "SMTP_START_TLS", True):
        cfg = mailer.MailConfig.from_settings()

    assert cfg is not None
    assert cfg.host == "smtp.qq.com"
    assert cfg.sender == "bot@qq.com"
    assert cfg.use_ssl is True
    # SSL 与 STARTTLS 同时开启时以 SSL 优先，避免双重加密
    assert cfg.start_tls is False


def test_send_email_raises_not_configured_when_host_missing():
    with patch.object(settings, "SMTP_HOST", ""):
        try:
            asyncio.run(mailer.send_email("主题", "<p>hi</p>", "hi", ["a@x.com"]))
        except mailer.MailNotConfigured:
            return
        raise AssertionError("预期抛出 MailNotConfigured")


def test_send_email_raises_not_configured_without_recipients():
    cfg = mailer.MailConfig(
        host="smtp.qq.com", port=465, user="bot@qq.com", password="code",
        sender="bot@qq.com", use_ssl=True, start_tls=False, timeout=5, max_retries=1,
    )
    with patch.object(settings, "DAILY_REPORT_RECIPIENTS", ""):
        try:
            asyncio.run(mailer.send_email("主题", None, "body", None, config=cfg))
        except mailer.MailNotConfigured:
            return
        raise AssertionError("预期抛出 MailNotConfigured")


def test_send_email_passes_recipients_to_smtp_layer():
    cfg = mailer.MailConfig(
        host="smtp.qq.com", port=465, user="bot@qq.com", password="code",
        sender="bot@qq.com", use_ssl=True, start_tls=False, timeout=5, max_retries=1,
    )
    captured: dict = {}

    def fake_send(cfg_arg, subject, html_body, text_body, recipients):
        captured.update(subject=subject, html=html_body, text=text_body, recipients=recipients)

    with patch.object(mailer, "_send_sync", fake_send):
        sent_to = asyncio.run(mailer.send_email("日报主题", "<p>html</p>", "text", ["a@x.com"], config=cfg))

    assert sent_to == ["a@x.com"]
    assert captured["subject"] == "日报主题"
    assert captured["recipients"] == ["a@x.com"]


def test_send_email_retries_then_succeeds():
    cfg = mailer.MailConfig(
        host="smtp.qq.com", port=465, user="bot@qq.com", password="code",
        sender="bot@qq.com", use_ssl=True, start_tls=False, timeout=5, max_retries=3,
    )
    attempts = {"count": 0}

    def flaky_send(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OSError("connection reset")

    with patch.object(mailer, "_send_sync", flaky_send), patch.object(mailer.asyncio, "sleep", _noop_async):
        sent_to = asyncio.run(mailer.send_email("主题", None, "body", ["a@x.com"], config=cfg))

    assert attempts["count"] == 3
    assert sent_to == ["a@x.com"]


def test_send_email_wraps_failure_after_exhausting_retries():
    cfg = mailer.MailConfig(
        host="smtp.qq.com", port=465, user="bot@qq.com", password="code",
        sender="bot@qq.com", use_ssl=True, start_tls=False, timeout=5, max_retries=2,
    )
    attempts = {"count": 0}

    def always_fail(*args, **kwargs):
        attempts["count"] += 1
        raise OSError("535 authentication failed")

    with patch.object(mailer, "_send_sync", always_fail), patch.object(mailer.asyncio, "sleep", _noop_async):
        try:
            asyncio.run(mailer.send_email("主题", None, "body", ["a@x.com"], config=cfg))
        except mailer.MailError as e:
            assert "535" in str(e)
        else:
            raise AssertionError("预期抛出 MailError")

    assert attempts["count"] == 2


async def _noop_async(*args, **kwargs):
    """替换 asyncio.sleep，避免测试真实等待退避时间。"""
    return None


def test_build_message_sets_headers_and_multipart_body():
    cfg = mailer.MailConfig(
        host="smtp.qq.com", port=465, user="bot@qq.com", password="code",
        sender="bot@qq.com", use_ssl=True, start_tls=False, timeout=5, max_retries=1,
    )
    message = mailer._build_message(cfg, "主题", "<p>html</p>", "text", ["a@x.com", "b@y.com"])

    assert message["From"] == "bot@qq.com"
    assert message["To"] == "a@x.com, b@y.com"
    assert message.is_multipart() is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. 定时任务（配置来源为数据库，未保存时回落环境变量）
# ─────────────────────────────────────────────────────────────────────────────

def _cfg(**overrides):
    """构造测试用生效配置：默认启用且 SMTP/收件人齐备，可按需覆盖任一字段。"""
    base = config_service.EffectiveReportConfig(
        source="test",
        enabled=True,
        hour=8,
        minute=0,
        recipients_raw="ops@x.com, lead@y.com",
        include_empty=True,
        smtp_host="smtp.test.local",
        smtp_port=465,
        smtp_user="bot@test.local",
        smtp_password="authcode",
        smtp_from="bot@test.local",
        smtp_use_ssl=True,
        smtp_start_tls=False,
        smtp_timeout_seconds=5,
        smtp_max_retries=1,
        password_from_database=True,
    )
    return dataclasses_replace(base, **overrides)


def _patch_config(cfg):
    async def _getter(session=None):
        return cfg

    return patch.object(config_service, "get_effective_config", _getter)


def test_job_skips_when_disabled():
    called = {"collect": False}

    async def fake_collect(report_date):
        called["collect"] = True
        return services.summarize([], report_date)

    with _patch_config(_cfg(enabled=False)), \
         patch.object(services, "collect_daily_report", fake_collect):
        asyncio.run(report_jobs.send_daily_report_job(REPORT_DATE))

    assert called["collect"] is False


def test_job_skips_when_no_recipients():
    called = {"collect": False}

    async def fake_collect(report_date):
        called["collect"] = True
        return services.summarize([], report_date)

    with _patch_config(_cfg(recipients_raw="")), \
         patch.object(services, "collect_daily_report", fake_collect):
        asyncio.run(report_jobs.send_daily_report_job(REPORT_DATE))

    assert called["collect"] is False


def test_job_skips_when_smtp_host_missing():
    called = {"collect": False}

    async def fake_collect(report_date):
        called["collect"] = True
        return services.summarize([], report_date)

    with _patch_config(_cfg(smtp_host="")), \
         patch.object(services, "collect_daily_report", fake_collect):
        asyncio.run(report_jobs.send_daily_report_job(REPORT_DATE))

    assert called["collect"] is False


def test_job_skips_empty_day_when_disabled_by_config():
    async def fake_collect(report_date):
        return services.summarize([], report_date)

    sent = {"count": 0}

    async def fake_send(*args, **kwargs):
        sent["count"] += 1
        return []

    with _patch_config(_cfg(include_empty=False)), \
         patch.object(services, "collect_daily_report", fake_collect), \
         patch.object(report_jobs, "send_email", fake_send):
        asyncio.run(report_jobs.send_daily_report_job(REPORT_DATE))

    assert sent["count"] == 0


def test_job_sends_report_on_happy_path():
    rows = [_row("t-1", "news_spider", "success")]
    captured: dict = {}

    async def fake_collect(report_date):
        return services.summarize(rows, report_date, records_by_task={"t-1": 42})

    async def fake_send(subject, html_body, text_body, recipients, config=None):
        captured.update(subject=subject, html=html_body, text=text_body, recipients=recipients, config=config)
        return recipients

    with _patch_config(_cfg()), \
         patch.object(services, "collect_daily_report", fake_collect), \
         patch.object(report_jobs, "send_email", fake_send):
        asyncio.run(report_jobs.send_daily_report_job(REPORT_DATE))

    assert captured["recipients"] == ["ops@x.com", "lead@y.com"]
    assert "2026-03-05" in captured["subject"]
    assert "news_spider" in captured["html"]
    assert "news_spider" in captured["text"]
    # 发送时使用配置解析出的 SMTP 参数（而非全局 settings）
    assert captured["config"].host == "smtp.test.local"
    assert captured["config"].sender == "bot@test.local"


def test_job_swallows_mail_errors():
    async def fake_collect(report_date):
        return services.summarize([_row("t-1", "news_spider", "success")], report_date)

    async def failing_send(*args, **kwargs):
        raise mailer.MailError("smtp down")

    with _patch_config(_cfg()), \
         patch.object(services, "collect_daily_report", fake_collect), \
         patch.object(report_jobs, "send_email", failing_send):
        # 不应抛出异常，保证其它定时任务不受影响
        asyncio.run(report_jobs.send_daily_report_job(REPORT_DATE))


def test_job_swallows_unexpected_errors():
    async def boom(report_date):
        raise RuntimeError("database unavailable")

    with _patch_config(_cfg()), \
         patch.object(services, "collect_daily_report", boom):
        asyncio.run(report_jobs.send_daily_report_job(REPORT_DATE))


# ─────────────────────────────────────────────────────────────────────────────
# 6. 调度器接线
#    集群只发一封的前提：APScheduler 仅 Leader 运行，且由「配置同步器」按需
#    注册 / 改点 / 移除日报任务（配置存于数据库，可随时从设置页面修改）。
# ─────────────────────────────────────────────────────────────────────────────

def _capture_scheduler_registration() -> dict:
    """在不启动调度器、不触碰 Redis JobStore 的前提下捕获 add_job 调用参数。"""
    import app.core.scheduler as scheduler_module

    captured: dict = {}

    def fake_add_job(*args, **kwargs):
        captured.setdefault("ids", []).append(kwargs.get("id"))
        captured.update(kwargs)
        captured["func"] = args[0] if args else kwargs.get("func")

    with patch.object(scheduler_module.scheduler, "add_job", fake_add_job), \
         patch.object(scheduler_module.scheduler, "start"):
        scheduler_module.start_scheduler()

    return captured


def test_scheduler_registers_report_config_syncer():
    captured = _capture_scheduler_registration()

    assert "report_settings_sync" in captured.get("ids", [])
    assert captured.get("trigger") == "interval"
    assert captured.get("seconds") == 60
    # 启动后立即同步一次，避免配置改动要等到下个周期才生效
    assert captured.get("next_run_time") is not None


def test_scheduler_does_not_register_daily_report_directly():
    # 日报任务由同步器按数据库配置注册，避免启动时读取不到最新配置
    captured = _capture_scheduler_registration()
    assert report_jobs.DAILY_REPORT_JOB_ID not in captured.get("ids", [])


def test_scheduler_still_registers_image_prune():
    # 新增同步器不得影响既有的镜像清理系统任务
    captured = _capture_scheduler_registration()
    assert "daily_image_prune" in captured.get("ids", [])


# ─────────────────────────────────────────────────────────────────────────────
# 7. 配置同步器（注册 / 改点 / 移除）
# ─────────────────────────────────────────────────────────────────────────────

class _FakeJob:
    """最小 Job 替身，仅保留同步器用到的 id / trigger 属性。"""

    def __init__(self, job_id, trigger=None):
        self.id = job_id
        self.trigger = trigger
        self.next_run_time = None


class _FakeScheduler:
    """记录 add/remove/reschedule 调用的假调度器。"""

    def __init__(self, jobs=None):
        self.jobs = {job.id: job for job in (jobs or [])}
        self.added: list[dict] = []
        self.removed: list[str] = []
        self.rescheduled: list[tuple[str, str]] = []

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_job(self, func, **kwargs):
        self.added.append(kwargs)
        trigger = CronTrigger(hour=kwargs.get("hour"), minute=kwargs.get("minute"))
        self.jobs[kwargs["id"]] = _FakeJob(kwargs["id"], trigger=trigger)

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self.jobs.pop(job_id, None)

    def reschedule_job(self, job_id, trigger=None):
        self.rescheduled.append((job_id, str(trigger)))
        self.jobs[job_id] = _FakeJob(job_id, trigger=trigger)


def _run_sync(cfg, fake_scheduler):
    import app.core.scheduler as scheduler_module

    with patch.object(scheduler_module, "scheduler", fake_scheduler), \
         _patch_config(cfg):
        asyncio.run(report_jobs.sync_report_schedule())
    return fake_scheduler


def test_sync_registers_job_when_enabled_and_missing():
    fake = _run_sync(_cfg(hour=6, minute=45), _FakeScheduler())

    assert len(fake.added) == 1
    assert fake.added[0]["id"] == report_jobs.DAILY_REPORT_JOB_ID
    assert fake.added[0]["hour"] == 6
    assert fake.added[0]["minute"] == 45
    assert fake.added[0]["replace_existing"] is True


def test_sync_removes_job_when_disabled():
    job = _FakeJob(report_jobs.DAILY_REPORT_JOB_ID)
    fake = _run_sync(_cfg(enabled=False), _FakeScheduler([job]))

    assert fake.removed == [report_jobs.DAILY_REPORT_JOB_ID]
    assert fake.added == []


def test_sync_does_nothing_when_disabled_and_job_absent():
    fake = _run_sync(_cfg(enabled=False), _FakeScheduler())

    assert fake.removed == []
    assert fake.added == []
    assert fake.rescheduled == []


def test_sync_reschedules_when_trigger_time_changed():
    job = _FakeJob(report_jobs.DAILY_REPORT_JOB_ID, trigger=CronTrigger(hour=3, minute=0))
    fake = _run_sync(_cfg(hour=9, minute=15), _FakeScheduler([job]))

    assert len(fake.rescheduled) == 1
    assert fake.rescheduled[0][0] == report_jobs.DAILY_REPORT_JOB_ID
    assert fake.added == []


def test_sync_keeps_job_when_trigger_time_matches():
    job = _FakeJob(report_jobs.DAILY_REPORT_JOB_ID, trigger=CronTrigger(hour=8, minute=0))
    fake = _run_sync(_cfg(hour=8, minute=0), _FakeScheduler([job]))

    assert fake.added == []
    assert fake.rescheduled == []
    assert fake.removed == []


def test_sync_survives_config_read_failure():
    import app.core.scheduler as scheduler_module

    async def failing_getter(session=None):
        raise RuntimeError("db down")

    fake = _FakeScheduler()
    with patch.object(scheduler_module, "scheduler", fake), \
         patch.object(config_service, "get_effective_config", failing_getter):
        asyncio.run(report_jobs.sync_report_schedule())

    assert fake.added == [] and fake.removed == []


def test_cron_matches_rejects_non_cron_trigger():
    from apscheduler.triggers.interval import IntervalTrigger

    assert report_jobs._cron_matches(IntervalTrigger(seconds=60), 8, 0) is False


def test_apply_schedule_now_skips_when_scheduler_not_running():
    import app.core.scheduler as scheduler_module

    class _StoppedScheduler:
        """未启动（非 Leader）的调度器替身。"""

        running = False

        def get_job(self, job_id):
            raise AssertionError("非 Leader 节点不应查询 JobStore")

    with patch.object(scheduler_module, "scheduler", _StoppedScheduler()):
        # 非 Leader 节点不应抛异常，交由 Leader 的周期同步器处理
        asyncio.run(report_jobs.apply_schedule_now())


def test_schedule_hint_describes_disabled_and_incomplete_states():
    assert "停用" in report_jobs.schedule_hint(_cfg(enabled=False))
    assert "SMTP 通道不完整" in report_jobs.schedule_hint(_cfg(smtp_password=""))
    assert "未配置收件人" in report_jobs.schedule_hint(_cfg(recipients_raw=""))
    assert "08:00" in report_jobs.schedule_hint(_cfg())


def test_get_schedule_status_when_scheduler_stopped():
    import app.core.scheduler as scheduler_module

    class _StoppedScheduler:
        """未启动（非 Leader）的调度器替身。"""

        running = False

        def get_job(self, job_id):
            raise AssertionError("调度器未运行时不应查询 JobStore")

    with patch.object(scheduler_module, "scheduler", _StoppedScheduler()):
        status = report_jobs.get_schedule_status()

    assert status["scheduler_active"] is False
    assert status["job_registered"] is False
    assert status["next_run_time"] is None


def test_mail_config_supports_starttls_mode():
    with patch.object(settings, "SMTP_HOST", "smtp.internal.corp"), \
         patch.object(settings, "SMTP_PORT", 587), \
         patch.object(settings, "SMTP_USER", "bot@corp.com"), \
         patch.object(settings, "SMTP_USE_SSL", False), \
         patch.object(settings, "SMTP_START_TLS", True):
        cfg = mailer.MailConfig.from_settings()

    assert cfg is not None
    assert cfg.port == 587
    assert cfg.use_ssl is False
    assert cfg.start_tls is True


# ─────────────────────────────────────────────────────────────────────────────
# 7. 数据库访问逻辑（用假引擎覆盖，无需真实数据库）
# ─────────────────────────────────────────────────────────────────────────────

def test_build_task_query_compiles_with_expected_predicates():
    from sqlalchemy.dialects import postgresql

    statement = services.build_task_query(datetime(2026, 3, 5), datetime(2026, 3, 6))
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "FROM spider_tasks" in sql
    assert "spider_tasks.created_at >=" in sql
    assert "spider_tasks.created_at <" in sql
    assert "spider_tasks.is_deleted = false" in sql
    assert "ORDER BY spider_tasks.created_at ASC" in sql


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _AsyncNoop:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, engine):
        self._engine = engine

    async def execute(self, statement, params=None):
        sql = str(statement)
        self._engine.executed.append((sql, params))
        if "information_schema.columns" in sql:
            return _FakeResult([(name,) for name in self._engine.tables])
        if 'FROM "' not in sql:
            # SET LOCAL statement_timeout 等非查询语句
            return _FakeResult([])

        table = sql.split('FROM "', 1)[1].split('"', 1)[0]
        if table in self._engine.fail_tables:
            raise RuntimeError("canceling statement due to statement timeout")
        return _FakeResult(self._engine.counts.get(table, []))

    def begin(self):
        return _AsyncNoop()


class _FakeConnectCtx:
    def __init__(self, engine):
        self._engine = engine

    async def __aenter__(self):
        return _FakeConn(self._engine)

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    """最小可用的异步引擎替身，用于验证数据量统计的表遍历与容错逻辑。"""

    def __init__(self, tables, counts, fail_tables=()):
        self.tables = list(tables)
        self.counts = counts
        self.fail_tables = set(fail_tables)
        self.executed: list[tuple[str, dict | None]] = []

    def connect(self):
        return _FakeConnectCtx(self)


def test_fetch_records_aggregates_across_tables_and_dedupes_task_ids():
    engine = _FakeEngine(
        tables=["news_data", "comments_data"],
        counts={
            "news_data": [("t-1", 10), ("t-2", 5)],
            "comments_data": [("t-1", 7)],
        },
    )
    with patch.object(services, "spider_async_engine", engine):
        counts, partial, skipped = asyncio.run(services.fetch_records_by_task(["t-1", "t-2", "t-1"]))

    assert counts == {"t-1": 17, "t-2": 5}
    assert partial is False
    assert skipped == []

    count_queries = [params for sql, params in engine.executed if "COUNT(*)" in sql]
    assert len(count_queries) == 2
    # 去重后传入，避免 SQL 参数膨胀
    assert count_queries[0]["ids"] == ["t-1", "t-2"]


def test_fetch_records_skips_failing_table_but_keeps_others():
    engine = _FakeEngine(
        tables=["broken_data", "news_data"],
        counts={"news_data": [("t-1", 3)]},
        fail_tables={"broken_data"},
    )
    with patch.object(services, "spider_async_engine", engine):
        counts, partial, skipped = asyncio.run(services.fetch_records_by_task(["t-1"]))

    assert counts == {"t-1": 3}
    assert partial is True
    assert skipped == ["broken_data"]


def test_fetch_records_reports_partial_when_table_catalog_unavailable():
    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("connection refused")

    with patch.object(services, "spider_async_engine", _BrokenEngine()):
        counts, partial, skipped = asyncio.run(services.fetch_records_by_task(["t-1"]))

    assert counts == {}
    assert partial is True
    assert skipped == []


def test_fetch_records_stops_at_global_timeout():
    engine = _FakeEngine(
        tables=["t_first", "t_second", "t_third"],
        counts={"t_first": [("t-1", 1)], "t_second": [("t-1", 2)], "t_third": [("t-1", 3)]},
    )

    class _FakeTime:
        """仅替换 services 模块内的 time 引用，避免影响 asyncio 内部对 monotonic 的使用。"""

        def __init__(self, values):
            self._values = list(values)

        def monotonic(self):
            return self._values.pop(0) if len(self._values) > 1 else self._values[0]

    # 第 1 次取 deadline，第 2 次判定首张表（未超时），第 3 次判定时已越过总超时
    with patch.object(services, "spider_async_engine", engine), \
         patch.object(services, "time", _FakeTime([0.0, 0.0, 1e9])):
        counts, partial, skipped = asyncio.run(services.fetch_records_by_task(["t-1"]))

    assert counts == {"t-1": 1}
    assert partial is True
    assert skipped == []


def test_to_row_skips_duration_when_never_started():
    from app.api.tasks.models import SpiderTask

    task = SpiderTask(
        task_id="t-1", spider_id=1, spider_name="news_spider", status="pending",
        created_at=datetime(2026, 3, 5, 0, 30),
    )
    row = services._to_row(task)

    assert row.bucket == "pending"
    assert row.duration_seconds is None
    assert row.started_at is None
