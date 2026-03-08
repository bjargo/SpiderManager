import sys
import os
import logging
from unittest.mock import patch, MagicMock

# Ensure logging outputs to console
logging.basicConfig(level=logging.INFO)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.container.image_manager import ImageManager

@patch("app.core.container.image_manager.docker.from_env")
def test_prune_images(mock_docker_env):
    mock_client = MagicMock()
    mock_docker_env.return_value = mock_client

    # Mock dangling prune response
    mock_client.images.prune.return_value = {
        "ImagesDeleted": [{"Deleted": "sha256:dangling1"}],
        "SpaceReclaimed": 1024000
    }

    # Mock spider images list filter
    mock_old_img = MagicMock()
    mock_old_img.attrs = {"Created": "2020-01-01T00:00:00.00000Z"}
    mock_old_img.tags = ["spider-old-project:latest"]
    mock_old_img.id = "sha256:old"

    mock_new_img = MagicMock()
    # Something very recent
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.00000Z")
    mock_new_img.attrs = {"Created": now_str}
    mock_new_img.tags = ["spider-new-project:latest"]
    mock_new_img.id = "sha256:new"

    mock_client.images.list.return_value = [mock_old_img, mock_new_img]

    manager = ImageManager()
    stats = manager.prune_images(days_old=7)

    print("Prune Stats:", stats)

    # Assertions
    assert stats["dangling_deleted"] == 1
    assert stats["spider_images_deleted"] == 1

    # ensure remove was called only once with the old image
    mock_client.images.remove.assert_called_once_with(image="sha256:old", force=True)
    print("Test passed! the old image was removed, and the new one was kept.")

if __name__ == "__main__":
    test_prune_images()
