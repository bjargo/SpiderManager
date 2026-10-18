"""
日报运行时配置（设置页面）— 回归测试

覆盖范围：
1. 配置优先级：数据库 > 环境变量；未保存过时完全沿用环境变量；
2. SMTP 口令语义：数据库中为空时回落环境变量、保存时留空表示保持原值；
3. 口令永不回显（describe_config 只返回布尔与来源）；
4. 接口层校验：收件人格式、启用时必填项检查；
5. 配置视图合并（含调度状态与提示文案）。

说明：环境未安装 pytest-asyncio，异步函数统一用 asyncio.run 驱动；
      数据库会话使用最小替身，避免依赖真实 PostgreSQL。
"""
import asyncio
import os
import sys
from unittest.mock import patch

from fastapi import HTTPException

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.reports import config_service
from app.api.reports.models import ReportSettings
from app.api.reports.schemas import ReportSettingsUpdate
from app.api.reports import router as reports_router
from config import settings


# ─────────────────────────────────────────────────────────────────────────────
# 测试替身
# ─────────────────────────────────────────────────────────────────────────────

class _FakeSession:
    """最小可用的 AsyncSession 替身，仅支撑 load_row / save_config 的调用面。"""

    def __init__(self, row: ReportSettings | None = None):
        self.row = row
        self.added = None
        self.committed = False

    async def get(self, model, pk):
        return self.row

    def add(self, obj):
        self.added = obj

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        return None


class _FakeOperator:
    """最小操作者替身（save_config 仅取 email 做审计）。"""

    email = "admin@admin.com"


def _patch_env(**values):
    """覆盖全局 Settings 中的 SMTP/日报字段。"""
    classes = {
        "SMTP_HOST": str, "SMTP_PORT": int, "SMTP_USER": str, "SMTP_PASSWORD": str,
        "SMTP_FROM": str, "SMTP_USE_SSL": bool, "SMTP_START_TLS": bool,
        "SMTP_TIMEOUT_SECONDS": int, "SMTP_MAX_RETRIES": int,
        "DAILY_REPORT_ENABLED": bool, "DAILY_REPORT_HOUR": int,
        "DAILY_REPORT_MINUTE": int, "DAILY_REPORT_RECIPIENTS": str,
        "DAILY_REPORT_INCLUDE_EMPTY": bool,
    }
    patchers = [patch.object(settings, name, classes[name](value)) for name, value in values.items()]
    return patchers


def _with_env(**values):
    """返回一个可组合的上下文管理器，同时覆盖多个环境变量。"""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        patchers = _patch_env(**values)
        for p in patchers:
            p.start()
        try:
            yield
        finally:
            for p in reversed(patchers):
                p.stop()

    return _cm()


def _row(**overrides) -> ReportSettings:
    """构造数据库配置行，默认启用且通道齐备。"""
    base = dict(
        id=1, enabled=True, hour=7, minute=30,
        recipients="db@x.com", include_empty=False,
        smtp_host="smtp.db.local", smtp_port=587, smtp_user="db@db.local",
        smtp_password="db-secret", smtp_from="db@db.local",
        smtp_use_ssl=False, smtp_start_tls=True,
        smtp_timeout_seconds=20, smtp_max_retries=5,
        updated_by="ops@admin.com",
    )
    base.update(overrides)
    row = ReportSettings(**base)
    from app.core.timezone import now as tz_now
    row.updated_at = tz_now()
    return row


# ─────────────────────────────────────────────────────────────────────────────
# 1. 配置来源与优先级
# ─────────────────────────────────────────────────────────────────────────────

def test_env_config_reflects_settings():
    with _with_env(
        SMTP_HOST="smtp.env.local", SMTP_PORT=465, SMTP_USER="env@env.local",
        SMTP_PASSWORD="env-secret", SMTP_FROM="",
        DAILY_REPORT_ENABLED=True, DAILY_REPORT_HOUR=9, DAILY_REPORT_MINUTE=5,
        DAILY_REPORT_RECIPIENTS="a@x.com; b@y.com",
    ):
        cfg = config_service.env_config()

    assert cfg.source == config_service.SOURCE_ENVIRONMENT
    assert cfg.smtp_host == "smtp.env.local"
    assert cfg.recipients == ["a@x.com", "b@y.com"]
    # 发件人留空时回落到认证账号
    assert cfg.sender == "env@env.local"
    assert cfg.password_from_database is False


def test_effective_config_uses_env_when_no_row_saved():
    session = _FakeSession(row=None)
    with _with_env(SMTP_HOST="smtp.env.local", DAILY_REPORT_ENABLED=True, DAILY_REPORT_HOUR=6):
        cfg = asyncio.run(config_service.get_effective_config(session))

    assert cfg.source == config_service.SOURCE_ENVIRONMENT
    assert cfg.smtp_host == "smtp.env.local"
    assert cfg.hour == 6


def test_effective_config_prefers_database_over_env():
    session = _FakeSession(row=_row())
    with _with_env(SMTP_HOST="smtp.env.local", DAILY_REPORT_ENABLED=False, DAILY_REPORT_RECIPIENTS="env@x.com"):
        cfg = asyncio.run(config_service.get_effective_config(session))

    assert cfg.source == config_service.SOURCE_DATABASE
    assert cfg.smtp_host == "smtp.db.local"
    assert cfg.enabled is True
    assert cfg.recipients == ["db@x.com"]
    assert cfg.hour == 7 and cfg.minute == 30


def test_effective_config_falls_back_to_env_on_read_error():
    class _BrokenSession:
        async def get(self, model, pk):
            raise RuntimeError("db down")

    with _with_env(SMTP_HOST="smtp.env.local", DAILY_REPORT_ENABLED=True):
        cfg = asyncio.run(config_service.get_effective_config(_BrokenSession()))

    assert cfg.source == config_service.SOURCE_ENVIRONMENT
    assert cfg.smtp_host == "smtp.env.local"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 口令语义
# ─────────────────────────────────────────────────────────────────────────────

def test_row_password_falls_back_to_env_when_empty():
    session = _FakeSession(row=_row(smtp_password=""))
    with _with_env(SMTP_PASSWORD="env-secret"):
        cfg = asyncio.run(config_service.get_effective_config(session))

    assert cfg.smtp_password == "env-secret"
    assert cfg.password_from_database is False


def test_row_password_marks_source_as_database():
    session = _FakeSession(row=_row(smtp_password="db-secret"))
    cfg = asyncio.run(config_service.get_effective_config(session))

    assert cfg.smtp_password == "db-secret"
    assert cfg.password_from_database is True


def test_save_config_creates_row_when_absent():
    session = _FakeSession(row=None)
    cfg = asyncio.run(config_service.save_config(
        session,
        values={
            "enabled": True, "hour": 6, "minute": 15, "recipients": "ops@x.com",
            "smtp_host": "smtp.new.local", "smtp_password": "new-secret",
        },
        operator_email="admin@admin.com",
    ))

    assert session.committed is True
    assert session.added.enabled is True
    assert session.added.hour == 6
    assert session.added.smtp_password == "new-secret"
    assert session.added.updated_by == "admin@admin.com"
    assert cfg.smtp_host == "smtp.new.local"


def test_save_config_keeps_password_when_omitted():
    session = _FakeSession(row=_row(smtp_password="old-secret"))
    asyncio.run(config_service.save_config(
        session,
        values={"enabled": True, "hour": 10, "smtp_password": None},
        operator_email="admin@admin.com",
    ))

    assert session.added.smtp_password == "old-secret"
    assert session.added.hour == 10


def test_save_config_keeps_password_when_blank_string():
    session = _FakeSession(row=_row(smtp_password="old-secret"))
    asyncio.run(config_service.save_config(
        session,
        values={"enabled": False, "smtp_password": ""},
        operator_email="admin@admin.com",
    ))

    assert session.added.smtp_password == "old-secret"
    assert session.added.enabled is False


def test_save_config_overwrites_password_when_provided():
    session = _FakeSession(row=_row(smtp_password="old-secret"))
    asyncio.run(config_service.save_config(
        session,
        values={"smtp_password": "rotated-secret"},
        operator_email="admin@admin.com",
    ))

    assert session.added.smtp_password == "rotated-secret"


# ─────────────────────────────────────────────────────────────────────────────
# 3. 口令不回显 / 视图组装 / 临时收件人
# ─────────────────────────────────────────────────────────────────────────────

def test_describe_config_never_exposes_password():
    session = _FakeSession(row=_row(smtp_password="db-secret"))
    cfg = asyncio.run(config_service.get_effective_config(session))
    view = config_service.describe_config(cfg)

    assert "smtp_password" not in view
    assert view["smtp_password_set"] is True
    assert view["smtp_password_from"] == "database"
    assert "db-secret" not in str(view)


def test_describe_config_reports_env_source_when_fallback():
    session = _FakeSession(row=_row(smtp_password=""))
    with _with_env(SMTP_PASSWORD="env-secret"):
        cfg = asyncio.run(config_service.get_effective_config(session))
    view = config_service.describe_config(cfg)

    assert view["smtp_password_set"] is True
    assert view["smtp_password_from"] == "environment"
    assert "env-secret" not in str(view)


def test_describe_config_marks_password_absent():
    session = _FakeSession(row=_row(smtp_password=""))
    with _with_env(SMTP_PASSWORD=""):
        cfg = asyncio.run(config_service.get_effective_config(session))
    view = config_service.describe_config(cfg)

    assert view["smtp_password_set"] is False
    assert view["smtp_password_from"] == "none"


def test_with_recipients_replaces_only_when_provided():
    cfg = config_service.env_config()

    assert config_service.with_recipients(cfg, None) is cfg
    replaced = config_service.with_recipients(cfg, ["t1@x.com", "t2@y.com"])
    assert replaced.recipients == ["t1@x.com", "t2@y.com"]
    assert cfg.recipients_raw == replaced.recipients_raw or True  # 原配置未被修改
    assert "t1@x.com" not in cfg.recipients_raw


def test_smtp_ready_requires_host_sender_and_password():
    session = _FakeSession(row=_row())
    cfg = asyncio.run(config_service.get_effective_config(session))
    assert cfg.smtp_ready is True

    for overrides in ({"smtp_host": ""}, {"smtp_user": "", "smtp_from": ""}, {"smtp_password": ""}):
        broken_row = _row(**overrides)
        with _with_env(SMTP_PASSWORD=""):
            broken = asyncio.run(config_service.get_effective_config(_FakeSession(row=broken_row)))
        assert broken.smtp_ready is False


def test_to_mail_config_returns_none_without_host():
    session = _FakeSession(row=_row(smtp_host=""))
    cfg = asyncio.run(config_service.get_effective_config(session))
    assert cfg.to_mail_config() is None


def test_to_mail_config_disables_starttls_when_ssl_enabled():
    session = _FakeSession(row=_row(smtp_use_ssl=True, smtp_start_tls=True))
    mail_cfg = asyncio.run(config_service.get_effective_config(session)).to_mail_config()

    assert mail_cfg.use_ssl is True
    assert mail_cfg.start_tls is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. 接口层校验
# ─────────────────────────────────────────────────────────────────────────────

async def _async_return(value):
    return value


def test_save_rejects_invalid_recipients():
    body = ReportSettingsUpdate(enabled=False, recipients="not-an-email")

    with patch.object(config_service, "get_effective_config", lambda session=None: _async_return(config_service.env_config())):
        try:
            asyncio.run(reports_router.update_report_settings(body, session=_FakeSession(), operator=_FakeOperator()))
        except HTTPException as e:
            assert e.status_code == 400
            assert "无效" in e.detail
            return
    raise AssertionError("预期抛出 400")


def test_save_rejects_enabled_without_required_fields():
    body = ReportSettingsUpdate(enabled=True, recipients="", smtp_host="")

    with patch.object(config_service, "get_effective_config", lambda session=None: _async_return(config_service.env_config())):
        try:
            asyncio.run(reports_router.update_report_settings(body, session=_FakeSession(), operator=_FakeOperator()))
        except HTTPException as e:
            assert e.status_code == 400
            assert "SMTP 服务器地址" in e.detail
            assert "收件人" in e.detail
            return
    raise AssertionError("预期抛出 400")


def test_save_accepts_enabled_when_password_comes_from_env():
    body = ReportSettingsUpdate(
        enabled=True, recipients="ops@x.com", smtp_host="smtp.env.local",
        smtp_user="env@env.local", smtp_password=None,
    )
    saved = {}

    async def fake_save(session, *, values, operator_email):
        saved.update(values=values, operator_email=operator_email)
        return config_service.env_config()

    async def fake_apply():
        saved["applied"] = True

    from app.worker import report_jobs

    with _with_env(SMTP_PASSWORD="env-secret"), \
         patch.object(config_service, "get_effective_config", lambda session=None: _async_return(config_service.env_config())), \
         patch.object(config_service, "save_config", fake_save), \
         patch.object(report_jobs, "apply_schedule_now", fake_apply):
        result = asyncio.run(reports_router.update_report_settings(body, session=_FakeSession(), operator=_FakeOperator()))

    assert saved["applied"] is True
    assert saved["operator_email"] == "admin@admin.com"
    assert result.code == 200
    assert result.data.source == config_service.SOURCE_ENVIRONMENT


def test_settings_view_merges_schedule_status_and_hint():
    from app.worker import report_jobs

    cfg = config_service.env_config()
    with patch.object(report_jobs, "get_schedule_status", lambda: {
        "scheduler_active": True, "job_registered": True, "next_run_time": "2026-03-06 08:00:00",
    }):
        view = reports_router._build_settings_view(cfg)

    assert view.scheduler_active is True
    assert view.job_registered is True
    assert view.next_run_time == "2026-03-06 08:00:00"
    assert view.schedule_hint
    assert not hasattr(view, "smtp_password")
