import sys
import os
import json
import tempfile
import logging
from unittest.mock import patch, MagicMock

logging.basicConfig(level=logging.INFO)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.container.image_manager import ImageManager, BuildKitUnavailable, ImageBuildError
from app.worker.executor import _format_build_error, _image_variant_hash

@patch("app.core.container.image_manager.docker.from_env")
@patch("app.core.container.runners.RunnerFactory.get_runner")
def test_build_args_passed_to_buildkit(mock_get_runner, mock_docker_env):
    mock_client = MagicMock()
    mock_docker_env.return_value = mock_client
    mock_runner = MagicMock()
    mock_get_runner.return_value = mock_runner

    manager = ImageManager()

    with tempfile.TemporaryDirectory() as build_dir:
        # 模拟 check_image_exists 返回 False, 这样才会走下面的构建逻辑
        with patch.object(manager, 'check_image_exists', return_value=False):
            manager.build_image(
                local_path=build_dir,
                language="python",
                image_tag="spider-test-token:abc",
                entrypoint="main.py",
                build_args={"PRIVATE_NPM_TOKEN": "secret-123"}
            )

        # BuildKit 主路径通过低层 API 提交，buildargs 必须以 JSON 形式透传
        build_call = mock_client.api._post.call_args
        params = build_call.kwargs["params"]
        assert params["version"] == "2"
        assert json.loads(params["buildargs"]) == {"PRIVATE_NPM_TOKEN": "secret-123"}
        mock_runner.prepare_context.assert_called_once_with(
            build_dir,
            {"entrypoint": "main.py", "buildkit_enabled": True},
        )

        # 主路径成功时不应回退到经典 builder
        mock_client.images.build.assert_not_called()
        print("Test passed! buildargs dict was correctly transmitted via BuildKit.")


@patch("app.core.container.image_manager.docker.from_env")
@patch("app.core.container.runners.RunnerFactory.get_runner")
def test_build_args_passed_to_legacy_builder_on_buildkit_failure(mock_get_runner, mock_docker_env):
    mock_client = MagicMock()
    mock_client.images.build.return_value = (MagicMock(), [])
    mock_docker_env.return_value = mock_client

    manager = ImageManager()

    with tempfile.TemporaryDirectory() as build_dir:
        with patch.object(manager, 'check_image_exists', return_value=False), \
             patch.object(manager, '_build_with_buildkit', side_effect=BuildKitUnavailable("buildkit disabled")):
            manager.build_image(
                local_path=build_dir,
                language="python",
                image_tag="spider-test-token:abc",
                entrypoint="main.py",
                build_args={"PRIVATE_NPM_TOKEN": "secret-123"}
            )

        # BuildKit 不可用时回退经典 builder，buildargs 仍需透传
        mock_client.images.build.assert_called_once_with(
            path=build_dir,
            tag="spider-test-token:abc",
            buildargs={"PRIVATE_NPM_TOKEN": "secret-123"},
            rm=True,
            forcerm=True
        )
        print("Test passed! buildargs dict was correctly transmitted to legacy builder.")


@patch("app.core.container.image_manager.docker.from_env")
@patch("app.core.container.runners.RunnerFactory.get_runner")
def test_buildkit_transport_failure_falls_back_to_legacy(mock_get_runner, mock_docker_env):
    from requests.exceptions import ConnectionError

    mock_client = MagicMock()
    mock_client.api._post.side_effect = ConnectionError("BuildKit endpoint unavailable")
    mock_client.images.build.return_value = (MagicMock(), [])
    mock_docker_env.return_value = mock_client

    manager = ImageManager()
    with tempfile.TemporaryDirectory() as build_dir:
        with patch.object(manager, "check_image_exists", return_value=False):
            manager.build_image(
                local_path=build_dir,
                language="python",
                image_tag="spider-buildkit-fallback:test",
                entrypoint="main.py",
            )

    mock_client.images.build.assert_called_once()


def test_build_error_includes_build_log_for_task_persistence():
    error = ImageBuildError("spider-example:tag", "pip could not resolve dependency")
    assert _format_build_error(error) == (
        "Build Failed: Failed to build Docker image spider-example:tag\n"
        "pip could not resolve dependency"
    )


def test_image_variant_hash_changes_with_build_args_without_exposing_them():
    token = "private-token-value"
    first = _image_variant_hash("python", "main.py", {"PRIVATE_TOKEN": token})
    second = _image_variant_hash("python", "main.py", {"PRIVATE_TOKEN": "rotated-token"})
    assert first != second
    assert token not in first


if __name__ == "__main__":
    test_build_args_passed_to_buildkit()
    test_build_args_passed_to_legacy_builder_on_buildkit_failure()
    test_buildkit_transport_failure_falls_back_to_legacy()
