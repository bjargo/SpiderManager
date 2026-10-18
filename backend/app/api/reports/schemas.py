"""每日任务统计日报 — 数据结构定义

注：类型别名 ``Date`` 用于避免与 ``SendDailyReportRequest.date`` 字段名冲突
（Python 3.14 的 PEP 649 延迟注解求值会将类命名空间纳入名称查找，导致字段名遮蔽同名类型）。
"""
from datetime import date as Date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReportTaskRow(BaseModel):
    """日报中的单个任务明细行"""

    task_id: str = Field(description="任务 UUID")
    spider_id: int = Field(description="爬虫 ID")
    spider_name: str = Field(description="爬虫名称（任务创建时快照）")
    status: str = Field(description="原始任务状态")
    bucket: str = Field(default="", description="归类结果: success / failed / cancelled / pending")
    node_id: Optional[str] = Field(default=None, description="执行节点 ID")
    created_at: datetime = Field(description="任务创建时间")
    started_at: Optional[datetime] = Field(default=None, description="任务开始时间")
    finished_at: Optional[datetime] = Field(default=None, description="任务结束时间")
    duration_seconds: Optional[float] = Field(default=None, description="执行耗时（秒）")
    records: int = Field(default=0, description="该任务已落库的采集数据条数")
    error_detail: Optional[str] = Field(default=None, description="异常信息（已截断）")


class SpiderRollup(BaseModel):
    """按爬虫聚合的成功运行汇总"""

    spider_name: str = Field(description="爬虫名称")
    runs: int = Field(description="当日成功运行次数")
    records: int = Field(description="当日成功运行累计采集条数")
    duration_seconds: Optional[float] = Field(default=None, description="累计耗时（秒）")
    task_ids: list[str] = Field(default_factory=list, description="关联任务 ID 列表")


class DailyReport(BaseModel):
    """每日任务统计日报的完整数据（预览接口直接返回该结构）"""

    report_date: Date = Field(description="报告归属日期")
    window_start: datetime = Field(description="统计窗口起点（含）")
    window_end: datetime = Field(description="统计窗口终点（不含）")
    generated_at: datetime = Field(description="报告生成时间")
    total_tasks: int = Field(description="窗口内任务总数")
    success_count: int = Field(description="成功任务数")
    failed_count: int = Field(description="失败任务数（failed / timeout / error）")
    cancelled_count: int = Field(description="用户取消的任务数")
    pending_count: int = Field(description="报告生成时仍未结束的任务数")
    success_rate: float = Field(description="成功率 = 成功 /（成功 + 失败）")
    total_records: int = Field(description="成功任务累计采集数据条数（已落库）")
    avg_duration_seconds: Optional[float] = Field(default=None, description="已结束任务平均耗时（秒）")
    success_spiders: list[SpiderRollup] = Field(default_factory=list, description="成功爬虫汇总")
    failed_tasks: list[ReportTaskRow] = Field(default_factory=list, description="失败任务明细")
    cancelled_tasks: list[ReportTaskRow] = Field(default_factory=list, description="取消任务明细")
    pending_tasks: list[ReportTaskRow] = Field(default_factory=list, description="未完成任务明细")
    records_partial: bool = Field(default=False, description="数据量统计是否不完整（存在超时/跳过）")
    records_skipped_tables: list[str] = Field(default_factory=list, description="统计失败被跳过的数据表")
    records_scope_note: str = Field(default="", description="数据量口径说明")


class SendDailyReportRequest(BaseModel):
    """手动发送日报的请求体"""

    date: Optional[Date] = Field(default=None, description="报告归属日期，默认为昨天")
    recipients: Optional[list[str]] = Field(
        default=None,
        description="临时收件人列表（仅本次生效，用于联调测试）；为空时使用 DAILY_REPORT_RECIPIENTS",
    )


class SendDailyReportResponse(BaseModel):
    """手动发送日报的结果"""

    report_date: Date = Field(description="报告归属日期")
    recipients: list[str] = Field(description="实际发送的收件人列表")
    total_tasks: int = Field(description="窗口内任务总数")
    success_count: int = Field(description="成功任务数")
    failed_count: int = Field(description="失败任务数")
    total_records: int = Field(description="成功任务累计采集条数")


class ReportSettingsUpdate(BaseModel):
    """日报配置的保存请求体"""

    enabled: bool = Field(description="是否启用每日日报")
    hour: int = Field(default=8, ge=0, le=23, description="发送时刻-小时")
    minute: int = Field(default=0, ge=0, le=59, description="发送时刻-分钟")
    recipients: str = Field(default="", description="收件人列表，逗号/分号分隔")
    include_empty: bool = Field(default=True, description="无任务日是否仍发送简报")
    smtp_host: str = Field(default="", description="SMTP 服务器地址")
    smtp_port: int = Field(default=465, ge=1, le=65535, description="SMTP 端口")
    smtp_user: str = Field(default="", description="SMTP 认证账号")
    smtp_password: Optional[str] = Field(
        default=None,
        description="SMTP 授权码；留空或省略表示保持原有口令不变（接口永不回显该字段）",
    )
    smtp_from: str = Field(default="", description="发件人地址，留空回落到 smtp_user")
    smtp_use_ssl: bool = Field(default=True, description="465 隐式 SSL")
    smtp_start_tls: bool = Field(default=False, description="587 STARTTLS")
    smtp_timeout_seconds: int = Field(default=15, ge=1, le=300, description="SMTP 超时（秒）")
    smtp_max_retries: int = Field(default=3, ge=1, le=10, description="发送失败重试次数")


class ReportSettingsOut(BaseModel):
    """日报配置的视图模型（不含口令明文）"""

    source: str = Field(description="配置来源: database（设置页面已保存）或 environment（环境变量）")
    enabled: bool = Field(description="是否启用每日日报")
    hour: int = Field(description="发送时刻-小时")
    minute: int = Field(description="发送时刻-分钟")
    recipients: str = Field(description="收件人原始字符串")
    recipient_list: list[str] = Field(description="解析后的收件人列表")
    include_empty: bool = Field(description="无任务日是否仍发送简报")
    smtp_host: str = Field(description="SMTP 服务器地址")
    smtp_port: int = Field(description="SMTP 端口")
    smtp_user: str = Field(description="SMTP 认证账号")
    smtp_password_set: bool = Field(description="是否已配置口令")
    smtp_password_from: str = Field(description="口令来源: database / environment / none")
    smtp_from: str = Field(description="发件人地址")
    smtp_use_ssl: bool = Field(description="是否使用隐式 SSL")
    smtp_start_tls: bool = Field(description="是否使用 STARTTLS")
    smtp_timeout_seconds: int = Field(description="SMTP 超时（秒）")
    smtp_max_retries: int = Field(description="重试次数")
    smtp_ready: bool = Field(description="SMTP 通道是否具备发送条件")
    updated_at: Optional[str] = Field(default=None, description="最后修改时间")
    updated_by: Optional[str] = Field(default=None, description="最后修改人")
    scheduler_active: bool = Field(default=False, description="当前节点是否正在运行调度器（仅 Leader 为 true）")
    job_registered: bool = Field(default=False, description="调度器中是否已注册日报任务")
    next_run_time: Optional[str] = Field(default=None, description="下次自动发送时间（本节点视角）")
    schedule_hint: str = Field(default="", description="调度状态的人类可读说明")


class TestMailRequest(BaseModel):
    """发送测试邮件的请求体"""

    recipients: Optional[list[str]] = Field(
        default=None,
        description="临时收件人（仅本次生效）；为空时使用当前配置的收件人列表",
    )


class TestMailResponse(BaseModel):
    """发送测试邮件的结果"""

    recipients: list[str] = Field(description="实际发送的收件人列表")
