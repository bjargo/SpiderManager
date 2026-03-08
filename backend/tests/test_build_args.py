import sys
import os
import logging
from unittest.mock import patch, MagicMock

logging.basicConfig(level=logging.INFO)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.container.image_manager import ImageManager

@patch("app.core.container.image_manager.docker.from_env")
@patch("app.core.container.runners.RunnerFactory.get_runner")
def test_build_args_passed_to_docker(mock_get_runner, mock_docker_env):
    mock_client = MagicMock()
    # Mock images.build 返回 2 个值：(image_obj, build_logs_generator)
    mock_client.images.build.return_value = (MagicMock(), [])
    mock_docker_env.return_value = mock_client
    
    manager = ImageManager()
    
    # 模拟 check_image_exists 返回 False, 这样才会走下面的构建逻辑
    with patch.object(manager, 'check_image_exists', return_value=False):
        
        # 准备一个包含 private_token 的构建参数
        manager.build_image(
            local_path="/tmp/fake_build",
            language="python",
            image_tag="spider-test-token:abc",
            entrypoint="main.py",
            build_args={"PRIVATE_NPM_TOKEN": "secret-123"}
        )

        # 检查 docker_client.images.build 的参数，特别是 buildargs
        mock_client.images.build.assert_called_once_with(
            path="/tmp/fake_build",
            tag="spider-test-token:abc",
            buildargs={"PRIVATE_NPM_TOKEN": "secret-123"},
            rm=True,
            forcerm=True
        )
        print("Test passed! buildargs dict was correctly transmitted to Docker engine.")

if __name__ == "__main__":
    test_build_args_passed_to_docker()
