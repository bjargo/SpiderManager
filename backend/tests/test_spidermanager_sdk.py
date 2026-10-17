import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SDK_PATH = (
    Path(__file__).parents[1]
    / "app"
    / "core"
    / "container"
    / "runtimes"
    / "python"
    / "spidermanager_sdk.py"
)
spec = importlib.util.spec_from_file_location("spidermanager_sdk", SDK_PATH)
spidermanager_sdk = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(spidermanager_sdk)


def test_sdk_posts_records_using_task_environment():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"data":{"count":1}}'
    request_seen = None

    def fake_urlopen(request, timeout):
        nonlocal request_seen
        request_seen = request
        assert timeout == 12
        return response

    data = [{"name": "item"}]
    with patch.object(spidermanager_sdk, "urlopen", fake_urlopen):
        result = spidermanager_sdk.SpiderManagerClient(
            base_url="http://master:8000/",
            task_id="task/1",
            timeout=12,
        ).send_data("products", data)

    assert result == {"data": {"count": 1}}
    assert isinstance(request_seen, Request)
    assert request_seen.full_url == (
        "http://master:8000/api/tasks/data/ingest?task_id=task%2F1"
    )
    assert json.loads(request_seen.data.decode("utf-8")) == {
        "table_name": "products",
        "data": data,
    }
    assert request_seen.get_method() == "POST"
