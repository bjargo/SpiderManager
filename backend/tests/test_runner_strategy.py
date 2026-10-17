import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker.docker_manager import DockerManager
from config import settings


@patch("app.worker.docker_manager.docker.from_env")
def test_run_spider_container(mock_from_env):
    mock_client = MagicMock()
    mock_from_env.return_value = mock_client
    mock_client.containers.run.return_value = MagicMock(short_id="container-id")

    manager = DockerManager()
    manager.run_spider_container(
        {
            "task_id": "test_runner_task_123",
            "image_tag": "spider-test-proj-456:fake123",
        }
    )

    run_args = mock_client.containers.run.call_args.kwargs
    assert run_args["image"] == "spider-test-proj-456:fake123"
    assert "command" not in run_args
    assert run_args["security_opt"] == ["no-new-privileges:true"]
    assert run_args["cap_drop"] == ["ALL"]
    assert run_args["pids_limit"] == settings.DOCKER_PIDS_LIMIT
    assert run_args["auto_remove"] is False
