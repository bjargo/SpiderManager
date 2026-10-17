import os
import shutil
import sys
import logging
from unittest.mock import patch, MagicMock
import docker

# Ensure logging outputs to console
logging.basicConfig(level=logging.INFO)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.container.image_manager import ImageManager

@patch("app.core.container.image_manager.docker.from_env")
def test_build_image(mock_from_env):
    # Setup mock docker client
    mock_client = MagicMock()
    mock_from_env.return_value = mock_client

    # Setup mock images build to return fake logs
    mock_client.images.build.return_value = (MagicMock(), [{"stream": "Step 1/3 : FROM python:3.11-slim\n"}])

    # Setup a dummy project directory
    test_dir = "./temp_test_build"
    os.makedirs(test_dir, exist_ok=True)

    with open(os.path.join(test_dir, "main.py"), "w") as f:
        f.write("print('Hello from Spider Container!')")

    with open(os.path.join(test_dir, "requirements.txt"), "w") as f:
        f.write("requests==2.31.0")

    manager = ImageManager()
    image_tag = "spider-manager-test:latest"

    print("--- First Build (Mocked) ---")
    mock_client.images.get.side_effect = docker.errors.ImageNotFound("mock not found")

    try:
        manager.build_image(
            local_path=test_dir,
            language="python",
            image_tag=image_tag,
            entrypoint="main.py"
        )

        # Verify files were created
        assert os.path.exists(os.path.join(test_dir, "Dockerfile")), "Dockerfile not found!"
        assert os.path.exists(os.path.join(test_dir, ".dockerignore")), ".dockerignore not found!"

        with open(os.path.join(test_dir, "Dockerfile"), "r", encoding="utf-8") as f:
            content = f.read()
            assert "WORKDIR /app" in content, "Incorrect Dockerfile content: WORKDIR /app missing"
            assert "COPY . /app/" in content, "Incorrect Dockerfile content: COPY . /app/ missing"
            assert "CMD main.py" in content, "Incorrect entrypoint in Dockerfile"

        # --- Second Build (Mocked - Image Exists) ---
        mock_client.images.get.side_effect = None # Remove the exception so it "exists"
        res = manager.build_image(
            local_path=test_dir,
            language="python",
            image_tag=image_tag,
            entrypoint="main.py"
        )
        assert res == image_tag
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    try:
        test_build_image()
    except Exception as e:
        import traceback
        traceback.print_exc()
