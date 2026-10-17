import asyncio
import logging
import os
import shutil
from typing import Any

from app.core.source.factory import SourceFactory

logger = logging.getLogger(__name__)


async def _remove_incomplete_project(project_dir: str) -> None:
    try:
        await asyncio.to_thread(shutil.rmtree, project_dir)
    except FileNotFoundError:
        pass
    except OSError as cleanup_error:
        logger.error(
            "Failed to remove incomplete source directory %s: %s",
            project_dir,
            cleanup_error,
        )


async def load_project(
    task_id: str,
    source_type: str,
    source_url: str,
    base_dir: str = "/tmp/crawlab_tasks",
    source_kwargs: dict[str, Any] | None = None,
) -> str:
    """Fetch a project through its SourceHandler into a task-scoped temporary directory."""
    project_dir = os.path.join(base_dir, task_id)
    os.makedirs(project_dir, exist_ok=True)

    try:
        fetch_task = asyncio.create_task(
            asyncio.to_thread(
                SourceFactory.get_handler(source_type).fetch,
                source_url,
                project_dir,
                **(source_kwargs or {}),
            )
        )
        await asyncio.shield(fetch_task)
        return project_dir
    except asyncio.CancelledError:
        try:
            await asyncio.shield(fetch_task)
        except Exception as fetch_error:
            logger.error("Source fetch failed while task was cancelling: %s", fetch_error)
        await _remove_incomplete_project(project_dir)
        raise
    except Exception:
        await _remove_incomplete_project(project_dir)
        raise
