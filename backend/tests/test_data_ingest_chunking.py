import asyncio
import json
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.tasks.schemas import DataIngestRequest
from app.api.tasks.services import ingest_data
from config import settings


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def lpush(self, key, *values):
        self.calls.append((key, values))


def test_ingest_splits_large_payload_into_bounded_queue_messages():
    async def _run():
        redis = FakeRedis()
        body = DataIngestRequest(
            table_name="products",
            data=[{"index": index} for index in range(5)],
        )

        from unittest.mock import patch
        with patch.object(settings, "REDUCER_BATCH_SIZE", 2):
            result = await ingest_data("task-1", body, redis)

        assert result == {"task_id": "task-1", "count": 5}
        assert len(redis.calls) == 1
        queue_key, messages = redis.calls[0]
        assert queue_key == settings.INGEST_QUEUE_KEY
        decoded = [json.loads(message) for message in messages]
        assert [len(message["d"]) for message in decoded] == [2, 2, 1]
        assert [row["index"] for message in decoded for row in message["d"]] == list(range(5))
        assert all(message["task_id"] == "task-1" for message in decoded)

    asyncio.run(_run())


def test_ingest_rejects_table_names_over_postgres_identifier_limit():
    with pytest.raises(ValidationError):
        DataIngestRequest(table_name="x" * 64, data=[{"value": 1}])
    with pytest.raises(ValidationError):
        DataIngestRequest(table_name="表" * 22, data=[{"value": 1}])
