"""
每日任务统计日报 — 业务逻辑层

职责：
1. 统计窗口计算与任务状态归类（纯函数，便于单元测试）；
2. 从主库聚合窗口内的任务明细（spider_tasks）；
3. 从爬虫数据专属库按 `_task_id` 统计各任务已落库的数据条数；
4. 渲染邮件主题 / HTML 正文 / 纯文本正文。

统计口径：
- 窗口：报告日 00:00:00（含）~ 次日 00:00:00（不含），应用时区的 naive datetime；
- 归属：spider_tasks.created_at 落在窗口内（与大盘 dashboard 口径保持一致）；
- 数据条数：仅统计已落库记录；单表统计超时/异常时跳过该表并在邮件中标注，保证日报整体可用。
"""
import logging
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import bindparam, text
from sqlmodel import select

from app.api.reports.schemas import DailyReport, ReportTaskRow, SpiderRollup
from app.api.tasks.models import SpiderTask
from app.core.sql import quote_identifier
from app.core.timezone import now as tz_now
from app.db.database import async_session_maker, spider_async_engine
from config import settings

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 状态归类集合（兼容美式拼写 canceled）
STATUS_SUCCESS = {"success"}
STATUS_FAILED = {"failed", "timeout", "error"}
STATUS_CANCELLED = {"cancelled", "canceled"}
STATUS_PENDING = {"pending", "running"}

# 邮件内单条错误信息最大长度，避免超长日志撑爆邮件
ERROR_DETAIL_MAX_LEN = 500

# 单表数据量统计的语句超时（毫秒），由 PostgreSQL 主动取消慢查询
COUNT_STATEMENT_TIMEOUT_MS = 10_000

RECORDS_SCOPE_NOTE = "数据条数为爬虫数据表中已落库的记录数（Redis 缓冲中尚未落库的数据不计入）"

_html_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True, trim_blocks=True, lstrip_blocks=True)
_text_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=False, trim_blocks=True, lstrip_blocks=True)


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数：窗口计算 / 状态归类 / 展示格式化
# ─────────────────────────────────────────────────────────────────────────────

def build_window(report_date: date) -> tuple[datetime, datetime]:
    """
    计算报告日对应的统计窗口 [起, 止)。

    :param report_date: 报告归属日期
    :return: (当天 00:00:00, 次日 00:00:00) 二元组
    """
    start = datetime.combine(report_date, dt_time.min)
    return start, start + timedelta(days=1)


def classify_status(status: str) -> str:
    """
    将任务原始状态归类到日报的四个统计桶。

    :param status: spider_tasks.status 原始值
    :return: "success" / "failed" / "cancelled" / "pending"（未知状态按 pending 归类并告警）
    """
    normalized = (status or "").strip().lower()
    if normalized in STATUS_SUCCESS:
        return "success"
    if normalized in STATUS_FAILED:
        return "failed"
    if normalized in STATUS_CANCELLED:
        return "cancelled"
    if normalized in STATUS_PENDING:
        return "pending"

    logger.warning("日报遇到未知任务状态 %r，按 pending 归类", status)
    return "pending"


def humanize_duration(seconds: float | None) -> str:
    """将秒数格式化为人类可读的时长文本。"""
    if seconds is None:
        return "-"

    total = int(round(seconds))
    if total < 60:
        return f"{total} 秒"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def format_int(value: int | None) -> str:
    """整数千分位格式化，便于阅读采集量。"""
    return f"{int(value or 0):,}"


def format_dt(value: datetime | None) -> str:
    """时间格式化，空值显示为 "-"。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def _truncate_error(error_detail: str | None) -> str | None:
    """截断超长错误信息。"""
    if not error_detail:
        return error_detail
    detail = error_detail.strip()
    if len(detail) <= ERROR_DETAIL_MAX_LEN:
        return detail
    return detail[:ERROR_DETAIL_MAX_LEN] + "...（已截断）"


# ─────────────────────────────────────────────────────────────────────────────
# 汇总（纯函数）
# ─────────────────────────────────────────────────────────────────────────────

def summarize(
    rows: list[ReportTaskRow],
    report_date: date,
    records_by_task: dict[str, int] | None = None,
    records_partial: bool = False,
    records_skipped_tables: list[str] | None = None,
    generated_at: datetime | None = None,
) -> DailyReport:
    """
    将任务明细汇总为日报数据。

    :param rows: 窗口内的任务明细行
    :param report_date: 报告归属日期
    :param records_by_task: 任务 ID → 已落库记录数
    :param records_partial: 数据量统计是否不完整
    :param records_skipped_tables: 统计失败被跳过的数据表
    :param generated_at: 报告生成时间，默认取当前时间
    :return: DailyReport 实例
    """
    counts = records_by_task or {}
    window_start, window_end = build_window(report_date)

    for row in rows:
        row.records = int(counts.get(row.task_id, 0))

    success_rows = [r for r in rows if r.bucket == "success"]
    failed_rows = [r for r in rows if r.bucket == "failed"]
    cancelled_rows = [r for r in rows if r.bucket == "cancelled"]
    pending_rows = [r for r in rows if r.bucket == "pending"]

    # 按爬虫聚合成功运行（采集数据量的主要呈现维度）
    agg: dict[str, dict] = {}
    for row in success_rows:
        entry = agg.setdefault(
            row.spider_name,
            {"runs": 0, "records": 0, "duration": 0.0, "has_duration": False, "task_ids": []},
        )
        entry["runs"] += 1
        entry["records"] += row.records
        if row.duration_seconds is not None:
            entry["duration"] += row.duration_seconds
            entry["has_duration"] = True
        entry["task_ids"].append(row.task_id)

    success_spiders = [
        SpiderRollup(
            spider_name=name,
            runs=entry["runs"],
            records=entry["records"],
            duration_seconds=round(entry["duration"], 2) if entry["has_duration"] else None,
            task_ids=entry["task_ids"],
        )
        for name, entry in agg.items()
    ]
    success_spiders.sort(key=lambda item: (-item.records, -item.runs, item.spider_name))

    finished = [r for r in rows if r.duration_seconds is not None]
    denominator = len(success_rows) + len(failed_rows)

    return DailyReport(
        report_date=report_date,
        window_start=window_start,
        window_end=window_end,
        generated_at=generated_at or tz_now(),
        total_tasks=len(rows),
        success_count=len(success_rows),
        failed_count=len(failed_rows),
        cancelled_count=len(cancelled_rows),
        pending_count=len(pending_rows),
        success_rate=round(len(success_rows) / denominator, 4) if denominator else 0.0,
        total_records=sum(r.records for r in success_rows),
        avg_duration_seconds=round(sum(r.duration_seconds for r in finished) / len(finished), 2) if finished else None,
        success_spiders=success_spiders,
        failed_tasks=sorted(failed_rows, key=lambda r: r.created_at),
        cancelled_tasks=sorted(cancelled_rows, key=lambda r: r.created_at),
        pending_tasks=sorted(pending_rows, key=lambda r: r.created_at),
        records_partial=records_partial,
        records_skipped_tables=records_skipped_tables or [],
        records_scope_note=RECORDS_SCOPE_NOTE,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 数据采集：主库任务明细 + 爬虫库数据量
# ─────────────────────────────────────────────────────────────────────────────

def _to_row(task: SpiderTask) -> ReportTaskRow:
    """将 SpiderTask ORM 对象转换为日报明细行。"""
    duration: float | None = None
    if task.started_at and task.finished_at:
        duration = max(0.0, (task.finished_at - task.started_at).total_seconds())

    return ReportTaskRow(
        task_id=task.task_id,
        spider_id=task.spider_id,
        spider_name=task.spider_name,
        status=task.status,
        bucket=classify_status(task.status),
        node_id=task.node_id,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        duration_seconds=duration,
        error_detail=_truncate_error(task.error_detail),
    )


def build_task_query(window_start: datetime, window_end: datetime):
    """
    构造统计窗口内的任务明细查询语句。

    时间比较使用应用时区的 naive datetime，与 ``SpiderTask.created_at`` 的写入格式保持一致。

    :param window_start: 窗口起点（含）
    :param window_end: 窗口终点（不含）
    :return: SQLModel/SQLAlchemy Select 语句
    """
    return (
        select(SpiderTask)
        .where(SpiderTask.created_at >= window_start)
        .where(SpiderTask.created_at < window_end)
        .where(SpiderTask.is_deleted == False)  # noqa: E712 —— SQLAlchemy 表达式必须用 == 比较
        .order_by(SpiderTask.created_at.asc())
    )


async def fetch_task_rows(window_start: datetime, window_end: datetime) -> list[ReportTaskRow]:
    """
    查询统计窗口内的任务明细（按创建时间归属，排除软删除记录）。

    :param window_start: 窗口起点（含）
    :param window_end: 窗口终点（不含）
    :return: ReportTaskRow 列表（按创建时间升序）
    """
    async with async_session_maker() as session:
        result = await session.execute(build_task_query(window_start, window_end))
        tasks = result.scalars().all()

    return [_to_row(task) for task in tasks]


async def _count_table_records(table: str, task_ids: list[str]) -> dict[str, int]:
    """
    统计单张数据表中指定任务集合的记录数（走既有 `_task_id` 索引）。

    :param table: 数据表名（取自 information_schema，非用户输入）
    :param task_ids: 任务 ID 列表
    :return: 任务 ID → 记录数
    """
    query = text(
        f'SELECT "_task_id" AS tid, COUNT(*) AS cnt FROM {quote_identifier(table)} '
        f'WHERE "_task_id" IN :ids GROUP BY "_task_id"'
    ).bindparams(bindparam("ids", expanding=True))

    async with spider_async_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text(f"SET LOCAL statement_timeout = {COUNT_STATEMENT_TIMEOUT_MS}"))
            result = await conn.execute(query, {"ids": task_ids})
            return {str(tid): int(cnt) for tid, cnt in result.all()}


async def fetch_records_by_task(task_ids: list[str]) -> tuple[dict[str, int], bool, list[str]]:
    """
    统计每个任务在爬虫数据库中已落库的记录数。

    实现要点：
    - 表清单取自 information_schema（仅包含存在 `_task_id` 列的数据表），单表一条
      `GROUP BY "_task_id"` 聚合查询，走既有 `_task_id` 索引；
    - 每张表使用独立连接与事务，单表失败（超时/结构异常）只跳过该表，不污染其它表统计；
    - 整体受 DAILY_REPORT_COUNT_TIMEOUT_SECONDS 总超时保护，超时按已统计部分返回。

    :param task_ids: 需要统计的任务 ID 列表
    :return: (任务ID → 记录数, 统计是否不完整, 统计失败被跳过的表名列表)
    """
    if not task_ids:
        return {}, False, []

    ids = list(dict.fromkeys(task_ids))
    counts: dict[str, int] = {}
    skipped: list[str] = []
    partial = False

    try:
        async with spider_async_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = '_task_id' "
                    "ORDER BY table_name"
                )
            )
            tables = [str(row[0]) for row in result.all()]
    except Exception as e:
        logger.error("读取爬虫数据表清单失败，跳过数据量统计: %s", e)
        return {}, True, []

    deadline = time.monotonic() + max(1, int(settings.DAILY_REPORT_COUNT_TIMEOUT_SECONDS))
    for index, table in enumerate(tables):
        if time.monotonic() > deadline:
            logger.warning(
                "数据量统计达到总超时（%ss），剩余 %d 张表未统计",
                settings.DAILY_REPORT_COUNT_TIMEOUT_SECONDS, len(tables) - index,
            )
            partial = True
            break
        try:
            table_counts = await _count_table_records(table, ids)
        except Exception as e:
            skipped.append(table)
            partial = True
            logger.warning("数据量统计跳过表 %s: %s", table, e)
            continue

        for task_id, count in table_counts.items():
            counts[task_id] = counts.get(task_id, 0) + count

    return counts, partial, skipped


# ─────────────────────────────────────────────────────────────────────────────
# 编排：生成日报数据与渲染邮件
# ─────────────────────────────────────────────────────────────────────────────

async def collect_daily_report(report_date: date) -> DailyReport:
    """
    生成指定日期的完整日报数据（不发送邮件）。

    :param report_date: 报告归属日期（如 2026-03-05 表示统计该日 00:00 ~ 24:00）
    :return: DailyReport 实例
    """
    window_start, window_end = build_window(report_date)
    rows = await fetch_task_rows(window_start, window_end)

    counts: dict[str, int] = {}
    partial = False
    skipped: list[str] = []
    if rows:
        counts, partial, skipped = await fetch_records_by_task([row.task_id for row in rows])

    return summarize(
        rows,
        report_date,
        records_by_task=counts,
        records_partial=partial,
        records_skipped_tables=skipped,
        generated_at=tz_now(),
    )


def build_subject(report: DailyReport) -> str:
    """构造邮件主题。"""
    if report.total_tasks == 0:
        return f"[SpiderManage] {report.report_date} 任务日报｜当日无任务"
    return (
        f"[SpiderManage] {report.report_date} 任务日报｜"
        f"成功 {report.success_count} / 失败 {report.failed_count} / 采集 {format_int(report.total_records)} 条"
    )


def render_report(report: DailyReport) -> tuple[str, str, str]:
    """
    渲染日报邮件内容。

    :param report: 日报数据
    :return: (主题, HTML 正文, 纯文本正文)
    """
    context = {
        "report": report,
        "fmt_duration": humanize_duration,
        "fmt_int": format_int,
        "fmt_dt": format_dt,
    }
    html_body = _html_env.get_template("daily_report.html.j2").render(**context)
    text_body = _text_env.get_template("daily_report.txt.j2").render(**context)
    return build_subject(report), html_body, text_body
