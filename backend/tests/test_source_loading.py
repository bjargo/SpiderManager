import asyncio
import os
import sys
import threading
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.source.archive import extract_zip_safely
from app.worker.project_loader import load_project


def test_load_project_uses_source_handler_and_forwards_branch(tmp_path):
    handler = MagicMock()
    source_kwargs = {"branch": "feature/test"}

    async def _run():
        with patch("app.worker.project_loader.SourceFactory.get_handler", return_value=handler):
            project_dir = await load_project(
                "task-1",
                "git",
                "https://example.com/repo.git",
                str(tmp_path),
                source_kwargs,
            )
        return project_dir

    project_dir = asyncio.run(_run())
    handler.fetch.assert_called_once_with(
        "https://example.com/repo.git",
        project_dir,
        branch="feature/test",
    )
    assert os.path.isdir(project_dir)


def test_load_project_removes_incomplete_directory(tmp_path):
    handler = MagicMock()
    handler.fetch.side_effect = RuntimeError("fetch failed")
    project_dir = tmp_path / "task-failure"

    async def _run():
        with patch("app.worker.project_loader.SourceFactory.get_handler", return_value=handler):
            await load_project("task-failure", "git", "https://example.com/repo.git", str(tmp_path))

    with pytest.raises(RuntimeError, match="fetch failed"):
        asyncio.run(_run())
    assert not project_dir.exists()


def test_load_project_cleans_directory_after_cancellation(tmp_path):
    started = threading.Event()
    release = threading.Event()
    handler = MagicMock()

    def blocking_fetch(url, destination):
        started.set()
        release.wait(timeout=5)

    handler.fetch.side_effect = blocking_fetch
    project_dir = tmp_path / "task-cancelled"

    async def _run():
        with patch("app.worker.project_loader.SourceFactory.get_handler", return_value=handler):
            task = asyncio.create_task(
                load_project("task-cancelled", "git", "https://example.com/repo.git", str(tmp_path))
            )
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            asyncio.get_running_loop().call_later(0.05, release.set)
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(_run())
    assert not project_dir.exists()


def test_extract_zip_safely_writes_regular_member(tmp_path):
    archive_data = BytesIO()
    with zipfile.ZipFile(archive_data, "w") as archive:
        archive.writestr("nested/main.py", "print('safe')")
    archive_data.seek(0)

    with zipfile.ZipFile(archive_data) as archive:
        extract_zip_safely(archive, str(tmp_path))

    assert (tmp_path / "nested" / "main.py").read_text(encoding="utf-8") == "print('safe')"


def test_extract_zip_safely_rejects_path_traversal(tmp_path):
    archive_data = BytesIO()
    with zipfile.ZipFile(archive_data, "w") as archive:
        archive.writestr("../escaped.txt", "unsafe")
    archive_data.seek(0)

    with zipfile.ZipFile(archive_data) as archive:
        with pytest.raises(ValueError, match="escapes extraction directory"):
            extract_zip_safely(archive, str(tmp_path))

    assert not (tmp_path.parent / "escaped.txt").exists()


def test_minio_source_fetch_streams_archive_to_temporary_file(tmp_path):
    from app.core.source.minio_handler import MinioSourceHandler

    archive_data = BytesIO()
    with zipfile.ZipFile(archive_data, "w") as archive:
        archive.writestr("main.py", "print('minio')")
    payload = archive_data.getvalue()

    class FakeMinioClient:
        def fget_object(self, bucket_name, object_name, file_path):
            with open(file_path, "wb") as output:
                output.write(payload)

    with patch("app.core.source.minio_handler.minio_manager.get_client", return_value=FakeMinioClient()), \
            patch("app.core.source.minio_handler.minio_manager.bucket_name", "bucket"):
        MinioSourceHandler().fetch("project.zip", str(tmp_path))

    assert (tmp_path / "main.py").read_text(encoding="utf-8") == "print('minio')"
    assert not list(tmp_path.glob("*.zip"))
