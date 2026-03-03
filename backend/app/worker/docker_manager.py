"""
DockerManager — DooD (Docker-outside-of-Docker) 爬虫容器管理模块

通过宿主机挂载的 /var/run/docker.sock，在 Worker 进程内使用 Docker SDK
动态创建、运行并监控爬虫容器，实现多语言爬虫的隔离执行。
"""
import logging
from typing import Any, Dict, Optional

import docker
from docker.errors import (
    APIError,
    ContainerError,
    DockerException,
    ImageNotFound,
    NotFound,
)

from config import settings

logger = logging.getLogger(__name__)


class DockerManager:
    """封装 Docker SDK 交互的管理器，通过依赖注入接收 docker client。"""

    def __init__(self, client: Optional[docker.DockerClient] = None) -> None:
        """
        初始化 DockerManager。

        Parameters
        ----------
        client : docker.DockerClient, optional
            外部注入的 Docker 客户端；不传则自动连接本机 Docker。
        """
        self._client = client

    @property
    def client(self) -> docker.DockerClient:
        """惰性初始化 Docker 客户端"""
        if self._client is None:
            try:
                self._client = docker.from_env()
                self._client.ping()
                logger.info("DockerManager connected to Docker daemon successfully.")
            except DockerException as exc:
                logger.error("Failed to connect to Docker daemon: %s", exc)
                raise
        return self._client

    # ─────────────────────────────────────────────
    # 核心方法：启动爬虫容器
    # ─────────────────────────────────────────────

    def run_spider_container(
        self,
        task_payload: Dict[str, Any],
        *,
        mem_limit: str | None = None,
        cpu_quota: int | None = None,
        cpu_period: int | None = None,
        network: str | None = None,
    ) -> docker.models.containers.Container:
        """
        根据 task_payload 启动一个爬虫容器并返回 Container 对象。

        容器内执行的工作流：
        1. wget / curl 从 MinIO 下载代码 ZIP 包
        2. unzip 解压到 /work
        3. cd 进入解压目录并执行 script_path 指定的命令

        Parameters
        ----------
        task_payload : dict
            至少包含:
            - task_id (str)
            - language (str)  : Docker 镜像名，如 "python:3.11-slim"
            - source_url (str): MinIO object key
            - script_path (str): 执行命令，如 "python main.py"
            - timeout_seconds (int)
        mem_limit : str
            容器内存上限，默认 512m
        cpu_quota : int
            CFS CPU 配额，默认 100000 (1 核)
        cpu_period : int
            CFS CPU 周期，默认 100000
        network : str
            接入的 Docker Network 名称

        Returns
        -------
        Container
            已启动的容器对象
        """
        # 从 settings 填充未指定的默认值
        mem_limit = mem_limit or settings.DOCKER_MEM_LIMIT
        cpu_quota = cpu_quota or settings.DOCKER_CPU_QUOTA
        cpu_period = cpu_period or settings.DOCKER_CPU_PERIOD
        network = network or settings.DOCKER_NETWORK

        task_id: str = task_payload["task_id"]
        image: str = task_payload.get("language", "python:3.11-slim")
        source_url: str = task_payload["source_url"]
        script_path: str = task_payload["script_path"]
        timeout_seconds: int = task_payload.get("timeout_seconds", 3600)

        # ── 构造 MinIO 下载地址 ──
        minio_endpoint = settings.MINIO_ENDPOINT
        bucket = settings.MINIO_BUCKET_NAME
        download_url = f"http://{minio_endpoint}/{bucket}/{source_url}"

        # ── 拼装自包含的 Shell 命令 ──
        # 工作流: 安装 unzip → 下载 ZIP → 解压 → 进入目录 → 执行脚本
        shell_command = (
            "set -e && "
            "mkdir -p /work && cd /work && "
            f"wget -q -O code.zip '{download_url}' && "
            "unzip -o -q code.zip && "
            "rm -f code.zip && "
            # 进入解压后的第一个目录（若 ZIP 包有顶级目录）或当前目录
            "cd $(ls -d */ 2>/dev/null | head -1 || echo '.') && "
            f"timeout {timeout_seconds} {script_path}"
        )

        container_name = f"spider-task-{task_id[:16]}"

        environment = {
            "TASK_ID": task_id,
            "PYTHONUNBUFFERED": "1",
        }

        logger.info(
            "Starting container for task %s | image=%s | network=%s | mem=%s",
            task_id, image, network, mem_limit,
        )

        try:
            container = self.client.containers.run(
                image=image,
                command=["sh", "-c", shell_command],
                name=container_name,
                environment=environment,
                network=network,
                mem_limit=mem_limit,
                cpu_period=cpu_period,
                cpu_quota=cpu_quota,
                auto_remove=True,
                detach=True,
            )
            logger.info(
                "Container %s (id=%s) started for task %s.",
                container_name, container.short_id, task_id,
            )
            return container

        except ImageNotFound:
            logger.error("Docker image '%s' not found. Attempting to pull...", image)
            try:
                self.client.images.pull(image)
                logger.info("Image '%s' pulled successfully. Retrying container run...", image)
                container = self.client.containers.run(
                    image=image,
                    command=["sh", "-c", shell_command],
                    name=container_name,
                    environment=environment,
                    network=network,
                    mem_limit=mem_limit,
                    cpu_period=cpu_period,
                    cpu_quota=cpu_quota,
                    auto_remove=True,
                    detach=True,
                )
                return container
            except (APIError, DockerException) as pull_err:
                logger.error("Failed to pull and run image '%s': %s", image, pull_err)
                raise

        except APIError as exc:
            logger.error("Docker API error when starting container for task %s: %s", task_id, exc)
            raise

    # ─────────────────────────────────────────────
    # 辅助方法
    # ─────────────────────────────────────────────

    def stop_container(self, container_name_or_id: str, timeout: int = 10) -> None:
        """停止指定容器"""
        try:
            container = self.client.containers.get(container_name_or_id)
            container.stop(timeout=timeout)
            logger.info("Container %s stopped.", container_name_or_id)
        except NotFound:
            logger.warning("Container %s not found, may have already been removed.", container_name_or_id)
        except APIError as exc:
            logger.error("Failed to stop container %s: %s", container_name_or_id, exc)

    def get_container_logs(
        self,
        container_name_or_id: str,
        *,
        stream: bool = True,
        follow: bool = True,
    ):
        """
        获取容器日志流。

        Parameters
        ----------
        container_name_or_id : str
        stream : bool
            为 True 返回生成器，逐行产出
        follow : bool
            为 True 持续跟踪新日志

        Returns
        -------
        generator | bytes
        """
        try:
            container = self.client.containers.get(container_name_or_id)
            return container.logs(stream=stream, follow=follow)
        except NotFound:
            logger.warning("Container %s not found.", container_name_or_id)
            return iter([])
        except APIError as exc:
            logger.error("Failed to fetch logs for container %s: %s", container_name_or_id, exc)
            return iter([])

    def close(self) -> None:
        """关闭 Docker 客户端连接"""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
