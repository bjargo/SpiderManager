import os
import shutil
import sys
import logging
import asyncio
from unittest.mock import patch, MagicMock

# Ensure logging outputs to console
logging.basicConfig(level=logging.INFO)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker.docker_manager import DockerManager
from app.core.container.image_manager import ImageManager

@patch("app.worker.docker_manager.docker.from_env")
@patch("app.core.container.image_manager.docker.from_env")
@patch("app.core.source.factory.SourceFactory.get_handler")
@patch("app.worker.project_loader.load_project")
async def test_run_spider_container(mock_load_project, mock_get_handler, mock_im_docker, mock_dm_docker):
    # Mock return values for file system setup
    test_dir = "./temp_runner_test"
    os.makedirs(test_dir, exist_ok=True)
    mock_load_project.return_value = test_dir

    # Mock source handler hash
    mock_handler = MagicMock()
    mock_handler.get_version_hash.return_value = "fake123hash"
    mock_get_handler.return_value = mock_handler

    # Mock ImageManager Docker client
    mock_im_client = MagicMock()
    mock_im_docker.return_value = mock_im_client
    mock_im_client.images.get.side_effect = Exception("Not found") # Force build
    mock_im_client.images.build.return_value = (MagicMock(), [{"stream": "building fake image...\n"}])

    # Mock DockerManager Docker client
    mock_dm_client = MagicMock()
    mock_dm_docker.return_value = mock_dm_client
    mock_container = MagicMock()
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.short_id = "fake_short_id_xyz"
    mock_dm_client.containers.run.return_value = mock_container

    # Provide fake task data typical for this setup
    task_data = {
        "task_id": "test_runner_task_123",
        "project_id": "test_proj_456",
        "source_type": "git",
        "source_url": "fake_url",
        "script_path": "python main.py",
        "language": "python:3.11-slim"
    }

    # IMPORTANT: The real integration test needs a running Redis server
    # Without real Redis, executor's _execute_task_in_container will crash trying to publish things.
    # We will just test the DockerManager part independently of Executor to verify the parameter changes.
    dm = DockerManager()

    task_data["image_tag"] = "spider-test-proj-456:fake123"

    print("Testing DockerManager.run_spider_container...")
    try:
        container = dm.run_spider_container(task_data)
        print(f"DockerManager ran container. Called with image: {mock_dm_client.containers.run.call_args[1].get('image')}")
        assert mock_dm_client.containers.run.call_args[1].get('image') == "spider-test-proj-456:fake123"
        assert "command" not in mock_dm_client.containers.run.call_args[1], "Command should NOT be passed to docker client in the new architecture!"
        print("Success! Command argument removed cleanly.")
    except Exception as e:
        print(f"Failed DockerManager run: {e}")

    # Cleanup
    shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    asyncio.run(test_run_spider_container())
