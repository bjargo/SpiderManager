import os
import shutil
import logging
import asyncio
import zipfile
from typing import Optional

from app.common.schemas.project import SourceType
from app.common.storage.minio_client import minio_manager

logger = logging.getLogger(__name__)

async def _download_and_extract_zip(source_url: str, dest_dir: str) -> None:
    """
    从 MinIO 下载项目 ZIP 并解压到 dest_dir。
    使用 MinIO 客户端直接下载，而非 presigned URL，避免 hostname 不一致的问题。
    """
    try:
        minio_manager.init_client()

        if not minio_manager.client:
            raise RuntimeError("MinIO client is not initialized")

        zip_path = os.path.join(dest_dir, "project.zip")

        # 直接通过 MinIO 客户端下载（在线程池中执行同步 IO，避免阻塞事件循环）
        await asyncio.get_running_loop().run_in_executor(
            None,
            minio_manager.client.fget_object,
            minio_manager.bucket_name,
            source_url,
            zip_path,
        )

        # 解压
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(dest_dir)

        # 清理 zip 包
        os.remove(zip_path)

        # 处理"单一顶层子目录"问题：
        # 当用户将整个文件夹打包成 zip 时（例如 `my_spider/main.py`），
        # 解压后 dest_dir 内只有一个子目录，而不是直接包含 main.py。
        # 这会导致 executor 以 dest_dir 为 cwd 执行 `python main.py` 时找不到文件。
        # 修复：检测到单一子目录时，将其内容提升至 dest_dir 根层级。
        entries = [e for e in os.listdir(dest_dir) if not e.startswith('.')]
        if len(entries) == 1 and os.path.isdir(os.path.join(dest_dir, entries[0])):
            single_subdir = os.path.join(dest_dir, entries[0])
            # 将子目录中的所有内容移到 dest_dir
            for item in os.listdir(single_subdir):
                src = os.path.join(single_subdir, item)
                dst = os.path.join(dest_dir, item)
                shutil.move(src, dst)
            shutil.rmtree(single_subdir)
            logger.info(f"Flattened single top-level directory '{entries[0]}' in {dest_dir}")
        logger.info(f"Successfully downloaded and extracted MINIO project to {dest_dir}")

    except Exception as e:
        logger.error(f"Failed to download/extract MINIO project: {e}")
        raise RuntimeError(f"Download Error: {str(e)}") from e


async def _clone_or_pull_git(source_url: str, dest_dir: str) -> None:
    try:
        # 如果目录已经存在 .git 就 pull，否则 clone
        if os.path.exists(os.path.join(dest_dir, ".git")):
            logger.info(f"Target directory {dest_dir} already contains a git repo. Pulling latest...")
            process = await asyncio.create_subprocess_exec(
                "git", "pull",
                cwd=dest_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        else:
            logger.info(f"Cloning git repo {source_url} to {dest_dir}...")
            # 确保 dest_dir 是空的
            if os.path.exists(dest_dir):
                shutil.rmtree(dest_dir)
            os.makedirs(dest_dir, exist_ok=True)
            
            process = await asyncio.create_subprocess_exec(
                "git", "clone", source_url, ".",
                cwd=dest_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore')
            logger.error(f"Git operation failed: {error_msg}")
            raise RuntimeError(f"Git Error: {error_msg}")
            
        logger.info(f"Successfully loaded GIT project to {dest_dir}")
        
    except Exception as e:
        logger.error(f"Failed to load GIT project: {e}")
        raise RuntimeError(f"Git Loading Error: {str(e)}") from e


async def load_project(task_id: str, source_type: str, source_url: str, base_dir: str = "/tmp/crawlab_tasks") -> str:
    """
    根据任务的 project 信息，拉取代码到临时目录并返回该工作目录的绝对路径。
    """
    project_dir = os.path.join(base_dir, task_id)
    os.makedirs(project_dir, exist_ok=True)
    
    try:
        if source_type == SourceType.MINIO.value or source_type == SourceType.MINIO:
            await _download_and_extract_zip(source_url, project_dir)
        elif source_type == SourceType.GIT.value or source_type == SourceType.GIT:
            await _clone_or_pull_git(source_url, project_dir)
        else:
            raise ValueError(f"Unsupported source type: {source_type}")
            
        return project_dir
        
    except Exception as e:
        # 如果加载过程出错，尽早清理产生的残余（如果有防爆满需求）
        # 这里可以选择不清理，交由外部 finally 统一处理
        raise e
