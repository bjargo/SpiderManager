import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# 将 backend 根目录加入 sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker.executor import _emit_log_pipeline, _refresh_log_hotbuf_ttl
from config import settings


def test_emit_log_pipeline_batch():
    """测试批量日志通过 pipeline 发布与热缓冲写入"""
    async def _run():
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, 1, 1, True, True])
        mock_redis.pipeline.return_value = mock_pipe

        task_id = "test-task-12345"
        channel = f"log:channel:{task_id}"
        lines = ["Line 1: init", "Line 2: processing", "Line 3: finished"]

        with patch("app.worker.executor.redis_manager") as mock_rm:
            mock_rm.client = mock_redis

            await _emit_log_pipeline(task_id, channel, lines)

            # 验证 pipeline 实例生成
            mock_redis.pipeline.assert_called_once_with(transaction=False)

            # 验证每个日志行都 publish 和 rpush
            assert mock_pipe.publish.call_count == 3
            assert mock_pipe.rpush.call_count == 3

            hotbuf_key = f"{settings.LOG_HOTBUF_PREFIX}{task_id}"
            mock_pipe.ltrim.assert_called_once_with(hotbuf_key, -settings.LOG_HOTBUF_MAX, -1)
            mock_pipe.expire.assert_called_once_with(hotbuf_key, settings.LOG_HOTBUF_TTL)
            mock_pipe.execute.assert_awaited_once()

    asyncio.run(_run())


def test_emit_log_pipeline_empty():
    """测试空日志列表时跳过处理"""
    async def _run():
        mock_redis = MagicMock()
        with patch("app.worker.executor.redis_manager") as mock_rm:
            mock_rm.client = mock_redis
            await _emit_log_pipeline("test-task-empty", "log:channel", [])
            mock_redis.pipeline.assert_not_called()

    asyncio.run(_run())


def test_emit_log_pipeline_error_handling():
    """测试 Redis 异常时精准捕获不向外抛出崩溃"""
    from redis.exceptions import TimeoutError as RedisTimeoutError

    async def _run():
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=RedisTimeoutError("Redis timed out"))
        mock_redis.pipeline.return_value = mock_pipe

        with patch("app.worker.executor.redis_manager") as mock_rm:
            mock_rm.client = mock_redis
            # 不应抛出异常，应被内部精准捕获并记录日志
            await _emit_log_pipeline("test-task-err", "channel", ["single line"])
            mock_pipe.execute.assert_awaited_once()

    asyncio.run(_run())


def test_refresh_log_hotbuf_ttl_uses_configured_replay_window():
    async def _run():
        mock_redis = MagicMock()
        mock_redis.expire = AsyncMock(return_value=True)

        with patch("app.worker.executor.redis_manager") as mock_rm:
            mock_rm.client = mock_redis
            await _refresh_log_hotbuf_ttl("task-with-history")

        mock_redis.expire.assert_awaited_once_with(
            f"{settings.LOG_HOTBUF_PREFIX}task-with-history",
            settings.LOG_HOTBUF_TTL,
        )

    asyncio.run(_run())
