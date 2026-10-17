"""
Data Reducer 引擎

常驻后台异步消费 Redis 数据队列，执行：
1. 实时数据分发：通过 Redis Pub/Sub 将数据推送给前端 WebSocket 订阅。
2. Postgres 智能入库：自动建表 + 批量 INSERT（数据以 JSONB 存储）。
3. 容错隔离：单条数据异常不影响整个 Reducer 进程。

消息来源：Redis List `settings.INGEST_QUEUE_KEY`（由 Gateway 接口 lpush 写入）
消息结构：{"t": table_name, "d": [row, ...], "task_id": "xxx", "ts": "..."}

存储策略：每条 row 以 JSONB 形式存入 `_data` 列，
爬虫采集的字段名可以随时变化而无需 ALTER TABLE。
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from app.core.sql import quote_identifier as _quote_identifier
from app.db.database import spider_async_engine as async_engine
from sqlalchemy import text
from redis.exceptions import RedisError
from app.core.redis import redis_manager
from config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────


@dataclass
class PendingRow:
    """单条待入库记录"""
    task_id: str
    data: dict[str, Any]


@dataclass
class TableBuffer:
    """按表维度的写入缓冲区"""
    task_id: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────
# 自动建表
# ─────────────────────────────────────────────────

# 内存缓存已知存在的表名，避免每次都查询 information_schema
_known_tables: set[str] = set()


async def _ensure_table_exists(table_name: str) -> None:
    """
    检查目标表是否存在；若不存在则自动创建。

    表结构固定为：
    - `_id`          BIGSERIAL PRIMARY KEY
    - `_task_id`     TEXT NOT NULL         ← 关联任务 ID（含索引）
    - `_data`        JSONB NOT NULL        ← 存储完整的采集数据
    - `_created_at`  TIMESTAMP DEFAULT NOW()

    使用 JSONB 的优势：
    - 爬虫字段名变化时无需 DDL 变更
    - 支持 GIN 索引加速 JSON 路径查询
    - 可通过 `_data->>'key'` 语法直接提取字段

    Args:
        table_name: 目标表名
    """
    if table_name in _known_tables:
        return

    async with async_engine.connect() as conn:
        # 查询 information_schema 判断表是否已存在
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :name"
            ),
            {"name": table_name},
        )
        if result.scalar_one_or_none() is not None:
            _known_tables.add(table_name)
            return

        # 固定表结构：JSONB 存储业务数据
        ddl = (
            f'CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} (\n'
            f'  "_id" BIGSERIAL PRIMARY KEY,\n'
            f'  "_task_id" TEXT NOT NULL,\n'
            f'  "_data" JSONB NOT NULL,\n'
            f'  "_created_at" TIMESTAMP DEFAULT NOW()\n'
            f')'
        )

        await conn.execute(text(ddl))

        # 为 _task_id 创建索引，加速按任务查询和清理
        safe_index_name = f"ix_{hashlib.sha256(table_name.encode('utf-8')).hexdigest()[:24]}_task_id"
        await conn.execute(text(
            f'CREATE INDEX IF NOT EXISTS {_quote_identifier(safe_index_name)} '
            f'ON {_quote_identifier(table_name)} ("_task_id")'
        ))

        await conn.commit()
        _known_tables.add(table_name)
        logger.info("Auto-created table '%s' with JSONB storage", table_name)


# ─────────────────────────────────────────────────
# 批量入库
# ─────────────────────────────────────────────────


async def _batch_insert(
    table_name: str,
    task_id: str,
    rows: list[dict[str, Any]],
) -> None:
    """
    将缓冲区中的数据批量写入 Postgres。

    每条 row 序列化为 JSON 字符串后以 JSONB 类型写入 `_data` 列，
    使用参数化的多行 INSERT 避免 SQL 注入。

    Args:
        table_name: 目标表名
        task_id: 关联任务 ID
        rows: 待写入的行列表（每个 dict 即为一条完整的采集数据）
    """
    if not rows:
        return



    # 构建参数化占位符:
    # INSERT INTO "table" ("_task_id", "_data")
    # VALUES (:tid_0, :data_0::jsonb), (:tid_1, :data_1::jsonb), ...
    value_groups: list[str] = []
    params: dict[str, str] = {}

    for idx, row in enumerate(rows):
        value_groups.append(f"(:tid_{idx}, CAST(:data_{idx} AS JSONB))")
        params[f"tid_{idx}"] = task_id
        params[f"data_{idx}"] = json.dumps(row, ensure_ascii=False)

    sql = (
        f'INSERT INTO {_quote_identifier(table_name)} ("_task_id", "_data") '
        f'VALUES {", ".join(value_groups)}'
    )

    async with async_engine.connect() as conn:
        await conn.execute(text(sql), params)
        await conn.commit()

    logger.debug(
        "Batch inserted %d rows into '%s' for task '%s'",
        len(rows), table_name, task_id,
    )


# ─────────────────────────────────────────────────
# 缓冲区 Flush 逻辑
# ─────────────────────────────────────────────────


async def _flush_buffer(
    buffer: dict[tuple[str, str], TableBuffer],
) -> int:
    """
    将所有按表和任务隔离的缓冲区批量写入数据库。

    容错策略：单表写入失败不影响其他表，且**失败表的数据保留在缓冲区中**
    等待下一轮重试（避免数据丢失）；仅清空写入成功的表。

    Args:
        buffer: 按表名和任务 ID 分组的缓冲区

    Returns:
        本次成功写入的行数
    """
    flushed = 0
    for buffer_key in list(buffer.keys()):
        table_name, task_id = buffer_key
        table_buf = buffer[buffer_key]
        if not table_buf.rows:
            buffer.pop(buffer_key, None)
            continue
        try:
            await _ensure_table_exists(table_name)
            await _batch_insert(table_name, table_buf.task_id, table_buf.rows)
        except Exception as e:
            # 容错：单表写入失败不影响其他表；保留缓冲等待重试，防止数据丢失
            logger.error(
                "Failed to flush %d rows into table '%s' (task=%s), 保留待重试: %s",
                len(table_buf.rows), table_name, task_id, e,
            )
            continue
        flushed += len(table_buf.rows)
        buffer.pop(buffer_key, None)

    return flushed


def _pending_rows(buffer: dict[tuple[str, str], TableBuffer]) -> int:
    """统计当前缓冲区中待入库的总行数。"""
    return sum(len(b.rows) for b in buffer.values())


def _table_pending_rows(buffer: dict[tuple[str, str], TableBuffer]) -> dict[str, int]:
    """统计各物理表（跨 task_id）的待入库行数。"""
    pending: dict[str, int] = {}
    for (table_name, _), table_buffer in buffer.items():
        pending[table_name] = pending.get(table_name, 0) + len(table_buffer.rows)
    return pending


# ─────────────────────────────────────────────────
# 主消费循环
# ─────────────────────────────────────────────────


async def data_reducer_worker() -> None:
    """
    常驻后台消费者：从 Redis 队列 `INGEST_QUEUE_KEY` 中消费数据，
    执行实时 Pub/Sub 分发 + 智能批量入库。

    退出条件：仅当 asyncio.CancelledError 被触发时优雅退出。
    """
    logger.info(
        "Data Reducer started | queue=%s | batch=%d | interval=%.1fs | "
        "high_watermark=%d | table_watermark=%d",
        settings.INGEST_QUEUE_KEY,
        settings.REDUCER_BATCH_SIZE,
        settings.REDUCER_FLUSH_INTERVAL,
        settings.REDUCER_MAX_PENDING_ROWS,
        settings.REDUCER_MAX_TABLE_ROWS,
    )

    # 按表名聚合的写入缓冲
    buffer: dict[tuple[str, str], TableBuffer] = {}
    last_flush_time = time.time()

    while True:
        try:
            if not redis_manager.client:
                await asyncio.sleep(1)
                continue

            # ── 背压保护 ──
            # 内存积压触及高水位（全局行数 或 单表行数）时，暂停从 Redis 拉取新数据，
            # 优先把缓冲区落库；若落库后仍高于水位（如数据库写入失败），
            # 则退避休眠后重试，从而避免无界积压导致 OOM。
            pending = _pending_rows(buffer)
            over_global = pending >= settings.REDUCER_MAX_PENDING_ROWS
            over_table = any(
                rows >= settings.REDUCER_MAX_TABLE_ROWS
                for rows in _table_pending_rows(buffer).values()
            )
            if over_global or over_table:
                await _flush_buffer(buffer)
                last_flush_time = time.time()
                remaining_pending = _pending_rows(buffer)
                still_over = (
                    remaining_pending >= settings.REDUCER_MAX_PENDING_ROWS
                    or any(
                        rows >= settings.REDUCER_MAX_TABLE_ROWS
                        for rows in _table_pending_rows(buffer).values()
                    )
                )
                if still_over:
                    # 落库后仍高于水位（多为 PostgreSQL 写入失败/过慢），
                    # 暂停消费并退避，等待下一次重试成功后再恢复。
                    logger.warning(
                        "Data Reducer 背压触发：内存待入库 %d 行（高水位 %d/%d），"
                        "暂停消费并退避 %.1fs",
                        remaining_pending,
                        settings.REDUCER_MAX_PENDING_ROWS,
                        settings.REDUCER_MAX_TABLE_ROWS,
                        settings.REDUCER_BACKPRESSURE_SLEEP,
                    )
                    await asyncio.sleep(settings.REDUCER_BACKPRESSURE_SLEEP)
                    continue
                logger.info(
                    "Data Reducer 背压解除：内存待入库降至 %d 行，恢复消费。",
                    remaining_pending,
                )

            # 非阻塞式弹出：用短超时的 BRPOP 避免 busy-loop，
            # 同时保证 flush_interval 到期时能及时触发写入
            remaining = max(
                0.1,
                settings.REDUCER_FLUSH_INTERVAL - (time.time() - last_flush_time),
            )
            result = await redis_manager.client.brpop(
                settings.INGEST_QUEUE_KEY, timeout=remaining,
            )

            if result:
                _, raw_message = result
                msg_str = raw_message if isinstance(raw_message, str) else raw_message.decode("utf-8")

                try:
                    msg: dict[str, Any] = json.loads(msg_str)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning("Invalid message in ingest queue, skipped: %s", e)
                    continue

                table_name: str = msg.get("t", "")
                data_rows = msg.get("d", [])
                if not isinstance(data_rows, list):
                    # 如果传进来的是单个 dict，包成 list
                    data_rows = [data_rows]
                task_id: str = msg.get("task_id", "unknown")

                if not table_name or not data_rows:
                    logger.warning("Empty table_name or data in message, skipped")
                    pass # Don't continue, let flush run if needed
                else:
                    # ── 1. 实时 Pub/Sub 分发 ──
                    try:
                        channel = f"data:channel:{task_id}"
                        # 注意: 要发单条数据还是数组？前端期望是一个完整的消息结构，或者前端期望 DataItem
                        # ingest_data 接口收到 body.data,
                        # 前端 ws.onmessage = (event) => rawMsg.d || rawMsg.data
                        # 这里分发全量消息体
                        await redis_manager.client.publish(channel, msg_str)
                    except RedisError as e:
                        logger.warning("Failed to publish data to channel for task %s: %s", task_id, e)

                    # ── 2. 缓冲区聚合 ──
                    buffer_key = (table_name, task_id)
                    if buffer_key not in buffer:
                        buffer[buffer_key] = TableBuffer(task_id=task_id)

                    table_buf = buffer[buffer_key]

                    for row in data_rows:
                        if isinstance(row, dict):
                            table_buf.rows.append(row)

            # ── 3. 判断是否需要 Flush ──
            now = time.time()
            pending = _pending_rows(buffer)
            should_flush = (
                pending >= settings.REDUCER_BATCH_SIZE
                or (pending > 0 and (now - last_flush_time) >= settings.REDUCER_FLUSH_INTERVAL)
            )

            if should_flush:
                await _flush_buffer(buffer)
                last_flush_time = time.time()

        except asyncio.CancelledError:
            # 优雅退出：flush 剩余数据
            pending = _pending_rows(buffer)
            logger.info("Data Reducer shutting down, flushing remaining %d rows...", pending)
            if pending > 0:
                await _flush_buffer(buffer)
            logger.info("Data Reducer stopped.")
            break

        except (ConnectionError, RedisError) as e:
            logger.error("Redis connection error in Data Reducer: %s", e)
            await asyncio.sleep(2)

        except Exception as e:
            logger.error("Unexpected error in Data Reducer: %s", e, exc_info=True)
            await asyncio.sleep(1)


async def start_data_reducer() -> asyncio.Task:
    """启动 Data Reducer 后台任务"""
    return asyncio.create_task(data_reducer_worker())
