import logging
from typing import BinaryIO, Optional
from datetime import timedelta
from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import MaxRetryError

from config import settings

logger = logging.getLogger(__name__)

class MinioClientManager:
    """
    MinIO 客户端管理器 (SRP & 防御性编程)
    提供独立无状态的文件操作能力
    """
    def __init__(self):
        self.client: Optional[Minio] = None
        self.bucket_name = settings.MINIO_BUCKET_NAME

    def init_client(self) -> None:
        """
        初始化 MinIO 客户端并确保默认 Bucket 存在。
        这由于是同步客户端实现（minio-python），可在 FastAPI lifespan 初始化。
        """
        if self.client is not None:
            return

        try:
            self.client = Minio(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ROOT_USER,
                secret_key=settings.MINIO_ROOT_PASSWORD,
                secure=False  # 根据实际情况可以开启 HTTPS
            )

            # 确保存储桶存在
            found = self.client.bucket_exists(self.bucket_name)
            if not found:
                self.client.make_bucket(self.bucket_name)
                logger.info(f"Created MinIO bucket: {self.bucket_name}")
            else:
                logger.debug(f"MinIO bucket '{self.bucket_name}' already exists.")
                
            logger.info("MinIO client initialized successfully.")
        except (S3Error, MaxRetryError, Exception) as e:
            logger.error(f"Failed to initialize MinIO client: {e}")
            raise RuntimeError(f"Storage Initialization Error: {str(e)}") from e

    def upload_stream(self, object_name: str, file_data: BinaryIO, length: int = -1) -> str:
        """
        流式上传 ZIP 产物。
        当 length=-1（文件大小未知）时，必须指定 part_size 以启用分块上传。
        """
        if not self.client:
            raise RuntimeError("MinIO client not initialized")
            
        try:
            # 当文件大小未知时，MinIO 要求指定 part_size (最小 5MB)
            kwargs: dict = {
                "bucket_name": self.bucket_name,
                "object_name": object_name,
                "data": file_data,
                "length": length,
            }
            if length == -1:
                kwargs["part_size"] = 10 * 1024 * 1024  # 10MB 分块

            result = self.client.put_object(**kwargs)
            logger.info(f"Successfully uploaded {object_name} to MinIO")
            return result.object_name
        except S3Error as e:
            logger.error(f"MinIO storage error during upload of {object_name}: {e}")
            raise RuntimeError(f"Storage Error: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error during upload of {object_name}: {e}")
            raise RuntimeError(f"Unexpected Storage Error: {str(e)}") from e

    def download_object(self, object_name: str) -> bytes:
        """
        从 MinIO 下载对象的完整字节内容
        """
        if not self.client:
            raise RuntimeError("MinIO client not initialized")

        response = None
        try:
            response = self.client.get_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
            )
            data = response.read()
            return data
        except S3Error as e:
            logger.error(f"MinIO storage error downloading {object_name}: {e}")
            raise RuntimeError(f"Storage Error: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error downloading {object_name}: {e}")
            raise RuntimeError(f"Unexpected Storage Error: {str(e)}") from e
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def upload_bytes(self, object_name: str, data: bytes) -> str:
        """
        将字节数据上传（覆盖）到 MinIO
        """
        if not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            from io import BytesIO
            stream = BytesIO(data)
            result = self.client.put_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                data=stream,
                length=len(data),
            )
            logger.info(f"Successfully uploaded {object_name} to MinIO ({len(data)} bytes)")
            return result.object_name
        except S3Error as e:
            logger.error(f"MinIO storage error during upload of {object_name}: {e}")
            raise RuntimeError(f"Storage Error: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error during upload of {object_name}: {e}")
            raise RuntimeError(f"Unexpected Storage Error: {str(e)}") from e

    def generate_presigned_url(self, object_name: str, expires_in_minutes: int = 15) -> str:
        """
        生成 Worker 能够临时下载的预签名 URL
        """
        if not self.client:
            raise RuntimeError("MinIO client not initialized")
            
        try:
            url = self.client.presigned_get_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                expires=timedelta(minutes=expires_in_minutes)
            )
            return url
        except S3Error as e:
            logger.error(f"MinIO storage error generating presigned url for {object_name}: {e}")
            raise RuntimeError(f"Storage Error: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error generating presigned url for {object_name}: {e}")
            raise RuntimeError(f"Unexpected Storage Error: {str(e)}") from e

    def generate_presigned_url_for_container(self, object_name: str, expires_in_minutes: int = 15) -> str:
        """
        生成爬虫容器内能够下载的预签名 URL
        即时创建一个针对 MINIO_CONTAINER_ENDPOINT 签发的临时客户端，解决 Host 不匹配导致 403 的问题
        """
        try:
            container_client = Minio(
                endpoint=settings.MINIO_CONTAINER_ENDPOINT,
                access_key=settings.MINIO_ROOT_USER,
                secret_key=settings.MINIO_ROOT_PASSWORD,
                secure=False
            )
            url = container_client.presigned_get_object(
                bucket_name=self.bucket_name,
                object_name=object_name,
                expires=timedelta(minutes=expires_in_minutes)
            )
            return url
        except S3Error as e:
            logger.error(f"MinIO storage error generating container presigned url for {object_name}: {e}")
            raise RuntimeError(f"Storage Error: {str(e)}") from e
        except Exception as e:
            logger.error(f"Unexpected error generating container presigned url for {object_name}: {e}")
            raise RuntimeError(f"Unexpected Storage Error: {str(e)}") from e

# 导出单例管理器
minio_manager = MinioClientManager()
