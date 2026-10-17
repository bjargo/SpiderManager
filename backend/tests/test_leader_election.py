"""
Leader 选举回归测试。

环境缺少 pytest-asyncio，故测试函数均为同步包装器，内部用 asyncio.run 驱动协程，
既能被 pytest 收集，也能直接 `python tests/test_leader_election.py` 运行。
"""
import asyncio
import logging
import os
import sys
import time
from unittest.mock import MagicMock, patch

logging.basicConfig(level=logging.INFO)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.core.scheduler as scheduler_module
from app.core.redis import redis_manager
from config import settings


class FakeRedis:
    """内存版 Redis，仅实现 Leader 选举所需语义：SET NX EX + 续约/释放 Lua 脚本。"""

    def __init__(self):
        self.store: dict = {}
        self.expires: dict = {}

    def _purge(self, key: str) -> None:
        exp = self.expires.get(key)
        if exp is not None and time.monotonic() >= exp:
            self.store.pop(key, None)
            self.expires.pop(key, None)

    async def set(self, key, value, nx=False, ex=None):
        self._purge(key)
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expires[key] = time.monotonic() + ex
        return True

    async def eval(self, script, numkeys, *keys_and_args):
        key, val = keys_and_args[0], keys_and_args[1]
        self._purge(key)
        if "expire" in script:      # 续约
            if self.store.get(key) == val:
                self.expires[key] = time.monotonic() + keys_and_args[2]
                return 1
            return 0
        if "del" in script:         # 释放
            if self.store.get(key) == val:
                self.store.pop(key, None)
                self.expires.pop(key, None)
                return 1
            return 0
        return 0

    # ── 测试辅助 ──
    def expire_now(self, key: str) -> None:
        self.expires[key] = time.monotonic() - 1

    def force_owner(self, key: str, value: str) -> None:
        self.store[key] = value
        self.expires.pop(key, None)


def _install(fake: FakeRedis, ttl: float = 1.0, renew: float = 0.05) -> None:
    redis_manager.client = fake
    scheduler_module.LEADER_TTL = ttl
    scheduler_module.LEADER_RENEW_INTERVAL = renew
    scheduler_module._is_leader = False
    scheduler_module._leader_task = None


def test_leader_lock_acquire_renew_and_takeover():
    """SET NX 保证同一时刻仅一个节点持锁；锁过期后备用节点立即接管。"""

    async def _run():
        fake = FakeRedis()
        _install(fake)

        # A 抢先获得锁
        with patch.object(settings, "NODE_ID", "node-A"):
            assert await scheduler_module._acquire_or_renew_leader() is True

            # B 抢不到
            with patch.object(settings, "NODE_ID", "node-B"):
                assert await scheduler_module._acquire_or_renew_leader() is False

            # A 续约成功
            assert await scheduler_module._acquire_or_renew_leader() is True

        # A 崩溃：锁自然过期，B 秒级接管
        fake.expire_now(scheduler_module.LEADER_KEY)
        with patch.object(settings, "NODE_ID", "node-B"):
            assert await scheduler_module._acquire_or_renew_leader() is True

        # A 恢复后无法续约，必须让出（避免双 Leader）
        with patch.object(settings, "NODE_ID", "node-A"):
            assert await scheduler_module._acquire_or_renew_leader() is False

    asyncio.run(_run())


def test_only_leader_starts_scheduler_and_steps_down():
    """续约多轮只启动一次 scheduler；丢失锁后立即停止调度。"""

    async def _run():
        fake = FakeRedis()
        _install(fake, renew=0.05)

        start_mock = MagicMock()
        stop_mock = MagicMock()

        with patch.object(scheduler_module, "start_scheduler", start_mock), \
                patch.object(scheduler_module, "shutdown_scheduler", stop_mock), \
                patch.object(settings, "NODE_ID", "node-A"):

            task = asyncio.create_task(scheduler_module._leader_election_loop())
            await asyncio.sleep(0.3)

            assert scheduler_module._is_leader is True
            assert start_mock.call_count == 1, f"应只启动一次，实际 {start_mock.call_count} 次"
            assert stop_mock.call_count == 0

            # 模拟锁被 B 抢走
            fake.force_owner(scheduler_module.LEADER_KEY, "node-B")
            await asyncio.sleep(0.3)

            assert scheduler_module._is_leader is False
            assert stop_mock.call_count == 1, f"应停止一次，实际 {stop_mock.call_count} 次"
            assert start_mock.call_count == 1, "失锁后不得再次启动"

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())


def test_stop_leader_election_releases_lock():
    """退出时应主动释放锁，便于备用节点立即接管。"""

    async def _run():
        fake = FakeRedis()
        _install(fake)

        stop_mock = MagicMock()
        with patch.object(scheduler_module, "shutdown_scheduler", stop_mock), \
                patch.object(settings, "NODE_ID", "node-A"):
            scheduler_module._is_leader = True
            fake.store[scheduler_module.LEADER_KEY] = "node-A"

            await scheduler_module.stop_leader_election()

            assert fake.store.get(scheduler_module.LEADER_KEY) is None, "锁应被释放"
            assert scheduler_module._is_leader is False
            assert stop_mock.call_count == 1

    asyncio.run(_run())


if __name__ == "__main__":
    test_leader_lock_acquire_renew_and_takeover()
    test_only_leader_starts_scheduler_and_steps_down()
    test_stop_leader_election_releases_lock()
    print("Leader election tests: ALL PASS")
