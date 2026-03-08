import os
import zipfile
import hashlib
from io import BytesIO
from .base import SourceHandler
from minio.error import S3Error
import logging
logger = logging.getLogger(__name__)
from app.core.storage.minio_client import minio_manager

class MinioSourceHandler(SourceHandler):
    def fetch(self, url: str, dest_dir: str, **kwargs) -> None:
        """
        从 MinIO 拉取并解压代码
        url: MinIO object_name (e.g., 'spiders/spider-xxx.zip')
        """
        logger.info(f"Downloading source from MinIO object: {url} to {dest_dir}...")
        try:
            # minio_manager 客户端的方法
            data = minio_manager.download_file(url)

            # 提取 ZIP 内容
            with zipfile.ZipFile(BytesIO(data)) as zf:
                zf.extractall(dest_dir)
            logger.info(f"Successfully downloaded and extracted MinIO object {url}.")
        except Exception as e:
            logger.error(f"Failed to fetch from MinIO {url}: {e}")
            raise e

    def get_version_hash(self, local_path: str) -> str:
        """
        获取基于文件内容的 Hash。包含相对路径信息和所有文件内容的 SHA256。
        忽略修改时间等元数据。
        """
        sha256 = hashlib.sha256()
        try:
            for root, dirs, files in os.walk(local_path):
                # 排序保证遍历顺序一致性
                dirs.sort()
                files.sort()
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, local_path)

                    # 混入相对路径确保位置变动也能导致 Hash 变动
                    sha256.update(rel_path.encode('utf-8'))

                    with open(file_path, 'rb') as f:
                        while chunk := f.read(8192):
                            sha256.update(chunk)

            version_hash = sha256.hexdigest()
            logger.info(f"Calculated version hash for {local_path}: {version_hash}")
            return version_hash
        except Exception as e:
            logger.error(f"Failed to calculate version hash for {local_path}: {e}")
            raise e

    def get_remote_fingerprint(self, url: str, **kwargs) -> str | None:
        """
        获取 MinIO 文件的 ETag。如果不带双引号则手动加上，确保一致性。
        """
        try:
            stat = minio_manager.client.stat_object(minio_manager.bucket_name, url)
            etag = stat.etag
            # MinIO 的 ETag 带有双引号，如 "d41d8cd98f00b204e9800998ecf8427e"
            if etag:
                etag = etag.strip('"')
            logger.info(f"Remote fingerprint for MinIO {url}: {etag}")
            return etag
        except S3Error as e:
            if e.code == "NoSuchKey":
                logger.warning(f"MinIO object {url} not found (NoSuchKey). Fingerprint unavailable.")
                return None
            logger.error(f"S3Error getting remote fingerprint for MinIO {url}: {e}")
            raise e
        except Exception as e:
            logger.error(f"Failed to get remote fingerprint for MinIO {url}: {e}")
            raise e
