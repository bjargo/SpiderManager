"""
Data Reducer 内存背压保护回归测试。

环境缺少 pytest-asyncio，故测试函数均为同步包装器，内部用 asyncio.run 驱动协程，
既能被 pytest 收集，也能直接 `python tests/test_data_reducer_backpressure.py` 运行。

覆盖点：
1. flush 失败的表数据保留在缓冲区等待重试，仅清空写入成功的表（避免数据丢失）。
2. 内存积压触及高水位且数据库不可写时，暂停消费 Redis 队列并退避（防止 OOM）。
3. 数据库恢复写入后自动解除背压、恢复消费。
"""
import asyncio
import json
import logging
import os
import sys
from unittest.mock import patch

logging.basicConfig(level=logging.WARNING)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.worker.data_reducer as data_reducer
from app.worker.data_reducer import TableBuffer
from app.core.redis import redis_manager
from config import settings


class FakeRedis:
    """内存版 Redis，仅实现 Reducer 消费循环所需的 brpop / publish。"""

    def __init__(self, messages):
        self.messages = list(messages)
        self.brpop_calls = 0
        self.published = []

    async def brpop(self, key, timeout=0):
        self.brpop_calls += 1
        if self.messages:
            return (key, self.messages.pop(0))
        # 队列空：模拟阻塞等待，避免 busy-loop
        await asyncio.sleep(0.005)
        return None

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1


class DBDouble:
    """可切换成功/失败的假数据库写入。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.inserted = 0

    async def ensure_table_exists(self, table_name):
        return None

    async def batch_insert(self, table_name, task_id, rows):
        if self.fail:
            raise RuntimeError("simulated postgres outage")
        self.inserted += len(rows)


def _patch_common(db: DBDouble):
    """统一 patch 掉真实建表/入库，替换为 DBDouble。"""
    return (
        patch.object(data_reducer, "_ensure_table_exists", db.ensure_table_exists),
        patch.object(data_reducer, "_batch_insert", db.batch_insert),
    )


def _messages(table: str, count: int):
    return [
        json.dumps({"t": table, "d": [{"i": i}], "task_id": "task-1"})
        for i in range(count)
    ]


def test_flush_buffer_retains_failed_table():
    """写入失败的表必须保留在缓冲区，写入成功的表才被清空。"""

    async def _run():
        buffer = {
            ("ok_table", "t"): TableBuffer(task_id="t", rows=[{"a": 1}, {"a": 2}]),
            ("bad_table", "t"): TableBuffer(task_id="t", rows=[{"b": 1}]),
        }

        async def ensure(table_name):
            return None

        async def insert(table_name, task_id, rows):
            if table_name == "bad_table":
                raise RuntimeError("boom")

        with patch.object(data_reducer, "_ensure_table_exists", ensure), \
                patch.object(data_reducer, "_batch_insert", insert):
            flushed = await data_reducer._flush_buffer(buffer)

        assert flushed == 2, f"仅应成功写入 2 行，实际 {flushed}"
        assert ("ok_table", "t") not in buffer, "写入成功的表应被清空"
        assert ("bad_table", "t") in buffer, "写入失败的表必须保留待重试"
        assert len(buffer[("bad_table", "t")].rows) == 1

    asyncio.run(_run())


def test_flush_buffer_keeps_task_ids_separate_for_shared_table():
    """同一张表的不同任务数据不能串用 `_task_id`。"""

    async def _run():
        buffer = {
            ("shared", "task-a"): TableBuffer(task_id="task-a", rows=[{"value": "a"}]),
            ("shared", "task-b"): TableBuffer(task_id="task-b", rows=[{"value": "b"}]),
        }
        inserted = []

        async def ensure(table_name):
            return None

        async def insert(table_name, task_id, rows):
            inserted.append((table_name, task_id, rows.copy()))

        with patch.object(data_reducer, "_ensure_table_exists", ensure), \
                patch.object(data_reducer, "_batch_insert", insert):
            flushed = await data_reducer._flush_buffer(buffer)

        assert flushed == 2
        assert {(table, task_id) for table, task_id, _ in inserted} == {
            ("shared", "task-a"),
            ("shared", "task-b"),
        }
        assert not buffer

    asyncio.run(_run())


def test_quote_identifier_escapes_embedded_quotes():
    assert data_reducer._quote_identifier('records"; DROP TABLE users;--') == (
        '"records""; DROP TABLE users;--"'
    )


def test_backpressure_pauses_consumption_when_db_unwritable():
    """积压达高水位且数据库持续写入失败时，必须停止拉取并退避。"""

    async def _run():
        fake = FakeRedis(_messages("t1", 500))
        db = DBDouble(fail=True)
        p_ensure, p_insert = _patch_common(db)

        with patch.object(redis_manager, "client", fake), p_ensure, p_insert, \
                patch.object(settings, "REDUCER_MAX_PENDING_ROWS", 50), \
                patch.object(settings, "REDUCER_MAX_TABLE_ROWS", 30), \
                patch.object(settings, "REDUCER_BATCH_SIZE", 1000), \
                patch.object(settings, "REDUCER_FLUSH_INTERVAL", 100.0), \
                patch.object(settings, "REDUCER_BACKPRESSURE_SLEEP", 0.02):

            task = asyncio.create_task(data_reducer.data_reducer_worker())
            await asyncio.sleep(0.25)
            calls_at_pause = fake.brpop_calls
            await asyncio.sleep(0.2)
            calls_later = fake.brpop_calls

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert calls_at_pause <= 55, f"达高水位后应停止拉取，实际已拉取 {calls_at_pause} 条"
        assert calls_later == calls_at_pause, (
            f"背压期间不得继续消费，pause 时 {calls_at_pause} -> 之后 {calls_later}"
        )

    asyncio.run(_run())


def test_backpressure_resumes_after_db_recovers():
    """数据库恢复后，缓冲区应落库成功并自动恢复消费。"""

    async def _run():
        fake = FakeRedis(_messages("t1", 500))
        db = DBDouble(fail=True)
        p_ensure, p_insert = _patch_common(db)

        with patch.object(redis_manager, "client", fake), p_ensure, p_insert, \
                patch.object(settings, "REDUCER_MAX_PENDING_ROWS", 50), \
                patch.object(settings, "REDUCER_MAX_TABLE_ROWS", 30), \
                patch.object(settings, "REDUCER_BATCH_SIZE", 1000), \
                patch.object(settings, "REDUCER_FLUSH_INTERVAL", 100.0), \
                patch.object(settings, "REDUCER_BACKPRESSURE_SLEEP", 0.02):

            task = asyncio.create_task(data_reducer.data_reducer_worker())
            await asyncio.sleep(0.25)
            calls_while_paused = fake.brpop_calls

            db.fail = False  # 数据库恢复
            await asyncio.sleep(0.3)
            calls_after_recovery = fake.brpop_calls

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert calls_after_recovery > calls_while_paused, (
            f"数据库恢复后应重新消费，pause 时 {calls_while_paused} "
            f"-> 恢复后 {calls_after_recovery}"
        )
        assert db.inserted > 0, "恢复后缓冲数据应成功落库"

    asyncio.run(_run())


if __name__ == "__main__":
    test_flush_buffer_retains_failed_table()
    test_flush_buffer_keeps_task_ids_separate_for_shared_table()
    test_quote_identifier_escapes_embedded_quotes()
    test_backpressure_pauses_consumption_when_db_unwritable()
    test_backpressure_resumes_after_db_recovers()
    print("Data Reducer backpressure tests: ALL PASS")
