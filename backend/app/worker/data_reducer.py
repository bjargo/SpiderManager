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
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
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
            f'CREATE TABLE IF NOT EXISTS "{table_name}" (\n'
            f'  "_id" BIGSERIAL PRIMARY KEY,\n'
            f'  "_task_id" TEXT NOT NULL,\n'
            f'  "_data" JSONB NOT NULL,\n'
            f'  "_created_at" TIMESTAMP DEFAULT NOW()\n'
            f')'
        )

        await conn.execute(text(ddl))

        # 为 _task_id 创建索引，加速按任务查询和清理
        safe_index_name = f"ix_{table_name}__task_id".replace('"', '')
        await conn.execute(text(
            f'CREATE INDEX IF NOT EXISTS "{safe_index_name}" '
            f'ON "{table_name}" ("_task_id")'
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
        f'INSERT INTO "{table_name}" ("_task_id", "_data") '
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
    buffer: dict[str, TableBuffer],
) -> None:
    """
    将所有按表聚合的缓冲区批量写入数据库。

    Args:
        buffer: 按表名分组的缓冲区
    """
    for table_name, table_buf in buffer.items():
        if not table_buf.rows:
            continue
        try:
            await _ensure_table_exists(table_name)
            await _batch_insert(table_name, table_buf.task_id, table_buf.rows)
        except Exception as e:
            # 容错：单表写入失败不影响其他表
            logger.error(
                "Failed to flush %d rows into table '%s' (task=%s): %s",
                len(table_buf.rows), table_name, table_buf.task_id, e,
            )

    buffer.clear()


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
        "Data Reducer started | queue=%s | batch=%d | interval=%.1fs",
        settings.INGEST_QUEUE_KEY,
        settings.REDUCER_BATCH_SIZE,
        settings.REDUCER_FLUSH_INTERVAL,
    )

    # 按表名聚合的写入缓冲
    buffer: dict[str, TableBuffer] = {}
    total_pending = 0
    last_flush_time = time.time()

    while True:
        try:
            if not redis_manager.client:
                await asyncio.sleep(1)
                continue

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
                data_rows: list[dict[str, Any]] = msg.get("d", [])
                task_id: str = msg.get("task_id", "unknown")

                if not table_name or not data_rows:
                    logger.warning("Empty table_name or data in message, skipped")
                    continue

                # ── 1. 实时 Pub/Sub 分发 ──
                try:
                    channel = f"data:channel:{task_id}"
                    await redis_manager.client.publish(channel, msg_str)
                except RedisError as e:
                    # Pub/Sub 失败不影响入库流程
                    logger.warning("Failed to publish data to channel for task %s: %s", task_id, e)

                # ── 2. 缓冲区聚合 ──
                if table_name not in buffer:
                    buffer[table_name] = TableBuffer()

                table_buf = buffer[table_name]
                table_buf.task_id = task_id

                for row in data_rows:
                    if isinstance(row, dict):
                        table_buf.rows.append(row)
                        total_pending += 1

            # ── 3. 判断是否需要 Flush ──
            now = time.time()
            should_flush = (
                total_pending >= settings.REDUCER_BATCH_SIZE
                or (total_pending > 0 and (now - last_flush_time) >= settings.REDUCER_FLUSH_INTERVAL)
            )

            if should_flush:
                await _flush_buffer(buffer)
                total_pending = 0
                last_flush_time = time.time()

        except asyncio.CancelledError:
            # 优雅退出：flush 剩余数据
            logger.info("Data Reducer shutting down, flushing remaining %d rows...", total_pending)
            if total_pending > 0:
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
