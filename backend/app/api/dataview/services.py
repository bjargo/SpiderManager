"""
数据查看模块 —— 服务层（只读 + 查询护栏）

提供爬虫采集数据表的浏览能力。为避免用户在页面上随意查询拖慢甚至拖垮
爬虫数据库（Spider DB），查询链路叠加了多层防护：

1. 表白名单    —— 表名必须通过标识符校验，且真实存在于 information_schema；
2. 只读事务    —— `SET TRANSACTION READ ONLY`，从根本上杜绝写操作；
3. 语句超时    —— `SET LOCAL statement_timeout`，超时由 PostgreSQL 直接取消查询；
4. 强制分页    —— limit 硬上限 + 游标分页（不接受 OFFSET，避免深翻页全表扫描）；
5. 并发闸门    —— 进程内信号量限制同时在跑的浏览查询数量，防止查询风暴占满连接池；
6. 估算优先    —— 总量取自 pg_class/pg_stat 统计信息，避免大表 COUNT(*)；
7. 元数据缓存  —— 表清单与统计信息短 TTL 缓存，降低系统表查询频率；
8. 慢查询告警  —— 超过阈值记录 WARNING，便于定位性能退化。

注意：本模块只读取连接池连接，不持有长事务；所有 SQL 的取值都走参数绑定，
只有经过校验的表名会拼进语句（并使用 quote_identifier 转义）。
"""
import asyncio
import contextlib
import logging
import re
import time
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import HTTPException, status as http_status
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.sql import quote_identifier
from app.core.timezone import now
from app.db.database import spider_async_engine

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 查询防护参数
# ─────────────────────────────────────────────────────────────────────────────
MAX_LIMIT = 100                 # 单次返回的最大行数
DEFAULT_LIMIT = 20              # 默认返回行数
STATEMENT_TIMEOUT_MS = 3000     # 单条 SQL 最大执行时间（毫秒），超时由数据库熔断
IDLE_TX_TIMEOUT_MS = 10_000     # 事务空闲上限，防止连接被长期占用
QUERY_SLOT_WAIT_SECONDS = 3.0   # 并发闸门最多等待的时间（秒）
MAX_CONCURRENT_QUERIES = 4      # 允许同时进行的浏览查询数量
METADATA_CACHE_TTL = 30.0       # 表清单/统计信息缓存时长（秒）
SAMPLE_ROWS_FOR_FIELDS = 50     # 字段预览的采样行数
MAX_SAMPLE_FIELDS = 50          # 字段预览最多展示的字段数
SLOW_QUERY_WARN_MS = 1000.0     # 慢查询告警阈值（毫秒）
MAX_RECENT_TASKS = 10           # 单表返回的最近任务数

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# 并发闸门：限制同时执行的浏览查询，避免多人同时刷新打满数据库连接
_query_slots = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

# 表清单/统计信息缓存：{"at": monotonic, "tables": {name: {...}}}
_table_cache: Dict[str, Any] = {"at": 0.0, "tables": {}}

GUARD_NOTE = (
    "所有查询均为只读且受限制：单次最多返回 {max_limit} 行、语句超时 {timeout}ms、"
    "并发上限 {concurrency}，并且仅支持游标分页（不支持跳页）。"
).format(
    max_limit=MAX_LIMIT,
    timeout=STATEMENT_TIMEOUT_MS,
    concurrency=MAX_CONCURRENT_QUERIES,
)


def guard_info() -> Dict[str, Any]:
    """返回当前生效的查询防护参数，供接口响应展示。"""
    return {
        "max_limit": MAX_LIMIT,
        "default_limit": DEFAULT_LIMIT,
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
        "max_concurrent_queries": MAX_CONCURRENT_QUERIES,
        "metadata_cache_ttl_seconds": METADATA_CACHE_TTL,
        "pagination": "cursor",
        "note": GUARD_NOTE,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数：参数校验与 SQL 构造（便于单元测试）
# ─────────────────────────────────────────────────────────────────────────────


def clamp_limit(limit: Optional[int]) -> int:
    """把请求的 limit 收敛到 [1, MAX_LIMIT]，防止通过超大 limit 拉取整表。"""
    if limit is None:
        return DEFAULT_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def validate_table_name(table_name: str) -> str:
    """
    校验数据表名合法性（仅允许 PostgreSQL 普通标识符）。

    :raises HTTPException: 400 —— 表名非法
    """
    name = (table_name or "").strip()
    if not _IDENTIFIER_RE.match(name):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="非法的数据表名（仅允许字母、数字、下划线，且以字母或下划线开头）",
        )
    return name


def normalize_order(order: Optional[str]) -> bool:
    """把排序参数归一化为是否倒序（desc=最新优先，默认）。"""
    return (order or "desc").strip().lower() != "asc"


def build_rows_sql(
    table_name: str,
    *,
    has_task_filter: bool,
    has_cursor: bool,
    descending: bool = True,
) -> str:
    """
    构造数据行分页 SQL。

    - 表名经校验后使用 quote_identifier 转义拼入语句；
    - 其余取值（limit / cursor / task_id）全部使用绑定参数，杜绝注入；
    - 仅按 `_id` 排序（主键索引），不接受用户自定义排序字段，避免额外排序开销。

    :param table_name: 已校验的数据表名
    :param has_task_filter: 是否按任务过滤（走 `_task_id` 索引）
    :param has_cursor: 是否使用游标（走主键索引，避免 OFFSET 深翻页）
    :param descending: True=最新优先
    """
    conditions: List[str] = []
    if has_task_filter:
        conditions.append('"_task_id" = :task_id')
    if has_cursor:
        conditions.append(f'"_id" {"<" if descending else ">"} :cursor')

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    order = "DESC" if descending else "ASC"
    return (
        'SELECT "_id", "_task_id", "_data", "_created_at" '
        f"FROM {quote_identifier(table_name)}{where_clause} "
        f'ORDER BY "_id" {order} LIMIT :limit'
    )


def build_task_count_sql(table_name: str) -> str:
    """构造按任务统计条数的 SQL（依赖 `_task_id` 索引，属于可控代价的精确统计）。"""
    return f'SELECT COUNT(*) FROM {quote_identifier(table_name)} WHERE "_task_id" = :task_id'


def _is_statement_timeout(exc: Exception) -> bool:
    """识别数据库语句超时（statement_timeout 熔断）异常。"""
    origin = getattr(exc, "orig", None)
    origin_name = type(origin).__name__ if origin is not None else ""
    if origin_name in {"QueryCanceledError", "QueryCanceled"}:
        return True
    message = str(exc).lower()
    return "statement timeout" in message or "canceling statement" in message


# ─────────────────────────────────────────────────────────────────────────────
# 受保护的只读查询连接
# ─────────────────────────────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def _guarded_query(wait_seconds: float = QUERY_SLOT_WAIT_SECONDS) -> AsyncIterator[AsyncConnection]:
    """
    获取一个"受保护"的爬虫库连接：

    - 先通过信号量限制并发，闸门满时快速失败（503），不排队堆积；
    - 事务级只读 + 语句超时 + 事务空闲超时；
    - 查询异常统一转换，超时返回 504 而不是 500。
    """
    try:
        await asyncio.wait_for(_query_slots.acquire(), timeout=wait_seconds)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据查询并发已满，请稍后重试",
        )

    started = time.perf_counter()
    try:
        async with spider_async_engine.connect() as conn:
            async with conn.begin():
                # 只读事务：任何写操作都会被数据库拒绝
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                # 语句级超时：由 PostgreSQL 主动取消慢查询，避免长时间占用连接
                await conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
                await conn.execute(text(f"SET LOCAL idle_in_transaction_session_timeout = {IDLE_TX_TIMEOUT_MS}"))
                yield conn
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        if _is_statement_timeout(exc):
            logger.warning(
                "数据查看查询被数据库熔断（statement_timeout=%sms）: %s",
                STATEMENT_TIMEOUT_MS,
                exc,
            )
            raise HTTPException(
                status_code=http_status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"查询超过 {STATEMENT_TIMEOUT_MS}ms 已被取消，请缩小查询范围",
            )
        logger.error("数据查看查询失败: %s", exc)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="数据查询失败",
        )
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > SLOW_QUERY_WARN_MS:
            logger.warning("慢查询告警：数据查看查询耗时 %.0fms", elapsed_ms)
        _query_slots.release()


# ─────────────────────────────────────────────────────────────────────────────
# 元数据（表清单 / 采集情况）
# ─────────────────────────────────────────────────────────────────────────────


async def _fetch_table_catalog() -> Dict[str, Dict[str, int]]:
    """
    读取爬虫库 public 模式下所有数据表及其统计信息。

    只访问系统表与统计视图（pg_class / pg_stat_user_tables），
    不做任何全表扫描，因此代价与表中数据量无关。
    """
    sql = text(
        """
        SELECT c.relname AS table_name,
               GREATEST(c.reltuples, COALESCE(s.n_live_tup, 0))::bigint AS estimated_rows,
               COALESCE(pg_total_relation_size(c.oid), 0)::bigint AS table_bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        """
    )
    async with _guarded_query() as conn:
        result = await conn.execute(sql)
        rows = result.fetchall()

    return {
        str(row[0]): {
            "estimated_rows": int(row[1] or 0),
            "table_bytes": int(row[2] or 0),
        }
        for row in rows
    }


async def _get_table_catalog_cached() -> Dict[str, Dict[str, int]]:
    """带 TTL 缓存的表清单（默认 30 秒），避免每次刷新都查系统表。"""
    cached_at = float(_table_cache.get("at") or 0.0)
    cached_tables = _table_cache.get("tables") or {}
    if cached_tables and (time.monotonic() - cached_at) < METADATA_CACHE_TTL:
        return cached_tables

    tables = await _fetch_table_catalog()
    _table_cache["at"] = time.monotonic()
    _table_cache["tables"] = tables
    return tables


async def _require_table(table_name: str) -> Dict[str, int]:
    """
    校验表存在并返回其统计信息（白名单校验，未登记的表直接 404）。

    :raises HTTPException: 404 —— 数据表不存在
    """
    catalog = await _get_table_catalog_cached()
    info = catalog.get(table_name)
    if info is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"数据表 {table_name} 不存在",
        )
    return info


async def _fetch_task_stats(
    session: AsyncSession,
    names: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    汇总平台库中各数据表的采集情况（任务数、成功数、最近采集时间、最近任务 ID）。

    聚合在平台库（而非爬虫库）执行，且只扫描平台任务表，代价与采集数据量无关。
    """
    params: Dict[str, Any] = {}
    where = "is_deleted = false"
    if names:
        where += " AND spider_name IN :names"
        params["names"] = list(names)
    sql = text(
        f"""
        SELECT spider_name,
               COUNT(*) AS task_count,
               COUNT(*) FILTER (WHERE status = 'success') AS success_count,
               MAX(COALESCE(finished_at, started_at, created_at)) AS last_collected_at
        FROM spider_tasks
        WHERE {where}
        GROUP BY spider_name
        """
    )
    if names:
        sql = sql.bindparams(bindparam("names", expanding=True))

    counts_result = await session.execute(sql, params)
    stats: Dict[str, Dict[str, Any]] = {
        str(row[0]): {
            "task_count": int(row[1] or 0),
            "success_count": int(row[2] or 0),
            "last_collected_at": row[3],
            "last_task_id": None,
        }
        for row in counts_result.fetchall()
    }

    # 最近一次采集对应的任务 ID（DISTINCT ON 只取每组首行，不聚合全部明细）
    latest_sql = text(
        f"""
        SELECT DISTINCT ON (spider_name) spider_name, task_id
        FROM spider_tasks
        WHERE {where}
        ORDER BY spider_name, COALESCE(finished_at, started_at, created_at) DESC
        """
    )
    if names:
        latest_sql = latest_sql.bindparams(bindparam("names", expanding=True))
    latest_result = await session.execute(
        latest_sql, {"names": list(names)} if names else {}
    )
    for spider_name, task_id in latest_result.fetchall():
        entry = stats.get(str(spider_name))
        if entry is not None:
            entry["last_task_id"] = str(task_id)

    return stats


async def _fetch_spider_map(session: AsyncSession) -> Dict[str, Dict[str, Any]]:
    """读取平台库爬虫元数据，用于把数据表关联回具体爬虫（表名 = 爬虫名）。"""
    result = await session.execute(
        text(
            "SELECT name, id, description FROM spiders WHERE is_deleted = false"
        )
    )
    return {
        str(row[0]): {"id": int(row[1]), "description": row[2]}
        for row in result.fetchall()
    }


async def _fetch_recent_tasks(
    session: AsyncSession,
    spider_name: str,
    limit: int = MAX_RECENT_TASKS,
) -> List[Dict[str, Any]]:
    """查询某数据表（爬虫）最近的任务记录，用于展示采集情况。"""
    result = await session.execute(
        text(
            """
            SELECT task_id, status, started_at, finished_at
            FROM spider_tasks
            WHERE spider_name = :spider_name AND is_deleted = false
            ORDER BY COALESCE(finished_at, started_at, created_at) DESC
            LIMIT :limit
            """
        ),
        {"spider_name": spider_name, "limit": limit},
    )
    return [
        {
            "task_id": str(row[0]),
            "status": str(row[1]),
            "started_at": row[2],
            "finished_at": row[3],
        }
        for row in result.fetchall()
    ]


async def _sample_fields(table_name: str) -> Dict[str, Any]:
    """
    采样少量最新记录以预览字段名。

    走主键索引倒序取 SAMPLE_ROWS_FOR_FIELDS 行（代价固定，与表大小无关）。
    """
    sql = text(
        f'SELECT "_data" FROM {quote_identifier(table_name)} ORDER BY "_id" DESC LIMIT :limit'
    )
    async with _guarded_query() as conn:
        result = await conn.execute(sql, {"limit": SAMPLE_ROWS_FOR_FIELDS})
        rows = result.fetchall()

    fields: Dict[str, None] = {}
    for row in rows:
        payload = row[0]
        if isinstance(payload, dict):
            for key in payload.keys():
                fields.setdefault(str(key), None)
                if len(fields) >= MAX_SAMPLE_FIELDS:
                    break
        if len(fields) >= MAX_SAMPLE_FIELDS:
            break

    return {"fields": list(fields), "sampled_rows": len(rows)}


async def _has_task_index(table_name: str) -> bool:
    """
    检查表上是否存在 `_task_id` 索引（缺失时按任务过滤会退化为全表扫描）。

    注意：pg_indexes.indexdef 中的列名不一定带双引号，需归一化后再匹配。
    """
    sql = text(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = :name"
    )
    async with _guarded_query() as conn:
        result = await conn.execute(sql, {"name": table_name})
        index_defs = [str(row[0] or "") for row in result.fetchall()]
    return any("_task_id" in index_def.replace('"', "") for index_def in index_defs)


# ─────────────────────────────────────────────────────────────────────────────
# 对外服务
# ─────────────────────────────────────────────────────────────────────────────


async def list_data_tables(session: AsyncSession) -> Dict[str, Any]:
    """
    列出所有数据表及其采集概况。

    :param session: 平台库会话（仅用于读取爬虫/任务元数据）
    :return: {"items": [...], "total_tables": N, "guard": {...}, "generated_at": ...}
    """
    catalog = await _get_table_catalog_cached()
    spider_map = await _fetch_spider_map(session)
    task_stats = await _fetch_task_stats(session)

    items: List[Dict[str, Any]] = []
    for table_name, info in catalog.items():
        stats = task_stats.get(table_name, {})
        spider = spider_map.get(table_name)
        items.append(
            {
                "table_name": table_name,
                "estimated_rows": info.get("estimated_rows", 0),
                "table_bytes": info.get("table_bytes", 0),
                "spider_id": spider["id"] if spider else None,
                "description": spider["description"] if spider else None,
                "task_count": stats.get("task_count", 0),
                "success_count": stats.get("success_count", 0),
                "last_collected_at": stats.get("last_collected_at"),
                "last_task_id": stats.get("last_task_id"),
            }
        )

    # 稳定排序：数据量倒序为次要键，最近采集时间倒序为主要键
    items.sort(key=lambda item: item["estimated_rows"], reverse=True)
    items.sort(key=lambda item: item["last_collected_at"] or datetime.min, reverse=True)

    return {
        "items": items,
        "total_tables": len(items),
        "guard": guard_info(),
        "generated_at": now(),
    }


async def get_table_overview(table_name: str, session: AsyncSession) -> Dict[str, Any]:
    """
    获取单个数据表的采集概况（统计信息 + 字段预览 + 最近任务）。

    :param table_name: 数据表名
    :param session: 平台库会话
    :raises HTTPException: 404 —— 表不存在
    """
    name = validate_table_name(table_name)
    info = await _require_table(name)

    stats = (await _fetch_task_stats(session, [name])).get(name, {})
    recent_tasks = await _fetch_recent_tasks(session, name)
    sample = await _sample_fields(name)
    has_task_index = await _has_task_index(name)

    return {
        "table_name": name,
        "estimated_rows": info.get("estimated_rows", 0),
        "table_bytes": info.get("table_bytes", 0),
        "task_count": stats.get("task_count", 0),
        "success_count": stats.get("success_count", 0),
        "last_collected_at": stats.get("last_collected_at"),
        "sample_fields": sample["fields"],
        "sampled_rows": sample["sampled_rows"],
        "has_task_index": has_task_index,
        "recent_tasks": recent_tasks,
        "guard": guard_info(),
    }


async def fetch_table_rows(
    table_name: str,
    *,
    limit: Optional[int],
    cursor: Optional[int],
    task_id: Optional[str],
    order: Optional[str],
    session: AsyncSession,
) -> Dict[str, Any]:
    """
    分页读取数据表内容（游标分页 + 多重限制）。

    :param table_name: 数据表名
    :param limit: 期望返回行数（会被收敛到 [1, MAX_LIMIT]）
    :param cursor: 上一页最后一行的 `_id`（不传则从最新/最早开始）
    :param task_id: 可选，按任务 ID 精确过滤（走 `_task_id` 索引）
    :param order: desc（最新优先，默认）或 asc（最早优先）
    :param session: 平台库会话（用于校验表存在）
    :raises HTTPException: 404 —— 表不存在；504 —— 查询超时被熔断
    """
    name = validate_table_name(table_name)
    info = await _require_table(name)

    max_rows = clamp_limit(limit)
    descending = normalize_order(order)
    has_task_filter = bool(task_id)
    has_cursor = cursor is not None

    sql = build_rows_sql(
        name,
        has_task_filter=has_task_filter,
        has_cursor=has_cursor,
        descending=descending,
    )
    # 多取一行用于判断是否还有下一页，避免额外 COUNT
    params: Dict[str, Any] = {"limit": max_rows + 1}
    if has_task_filter:
        params["task_id"] = task_id
    if has_cursor:
        params["cursor"] = cursor

    started = time.perf_counter()
    async with _guarded_query() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.fetchall()

        # 仅在按任务过滤（走索引）时提供精确条数，避免大表 COUNT(*)
        exact_total: Optional[int] = None
        if has_task_filter:
            count_result = await conn.execute(
                text(build_task_count_sql(name)), {"task_id": task_id}
            )
            exact_total = int(count_result.scalar() or 0)
    query_ms = (time.perf_counter() - started) * 1000

    has_more = len(rows) > max_rows
    page_rows = rows[:max_rows]
    items = [
        {
            "id": int(row[0]),
            "task_id": str(row[1]),
            "data": row[2] if isinstance(row[2], dict) else {},
            "created_at": row[3],
        }
        for row in page_rows
    ]

    return {
        "table_name": name,
        "items": items,
        "limit": max_rows,
        "next_cursor": items[-1]["id"] if items else None,
        "has_more": has_more,
        "estimated_total": info.get("estimated_rows", 0),
        "exact_total": exact_total,
        "order": "desc" if descending else "asc",
        "query_ms": round(query_ms, 1),
        "guard": guard_info(),
    }
