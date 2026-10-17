"""Small standard-library client for submitting crawler data to SpiderManager."""

import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SpiderManagerClient:
    def __init__(
        self,
        base_url: str | None = None,
        task_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("SPIDER_API_URL", "")).rstrip("/")
        self.task_id = task_id or os.getenv("TASK_ID", "")
        if not self.base_url:
            raise RuntimeError("SPIDER_API_URL is not set")
        if not self.task_id:
            raise RuntimeError("TASK_ID is not set")
        self.timeout = timeout

    def send_data(self, table_name: str, data: list[dict[str, Any]]) -> dict[str, Any]:
        if not table_name:
            raise ValueError("table_name must not be empty")
        if not data:
            raise ValueError("data must contain at least one record")

        url = (
            f"{self.base_url}/api/tasks/data/ingest?"
            f"{urlencode({'task_id': self.task_id})}"
        )
        payload = json.dumps(
            {"table_name": table_name, "data": data},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def send_data(table_name: str, data: list[dict[str, Any]]) -> dict[str, Any]:
    """Submit records using TASK_ID and SPIDER_API_URL from the container environment."""
    return SpiderManagerClient().send_data(table_name, data)
