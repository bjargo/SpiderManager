"""
DockerManager — DooD (Docker-outside-of-Docker) 爬虫容器管理模块

通过宿主机挂载的 /var/run/docker.sock，在 Worker 进程内使用 Docker SDK
动态创建、运行并监控爬虫容器，实现多语言爬虫的隔离执行。
"""
import logging
import socket
from urllib.parse import urlparse
from typing import Any, Dict, Optional
from app.core.storage.minio_client import minio_manager

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

    def _resolve_master_host_mapping(self) -> Dict[str, str]:
        """
        解析 settings.SPIDER_API_URL 中的主机名并获取其公网 IP。
        返回用于 Docker extra_hosts 的字典映射。
        """
        try:
            parsed_url = urlparse(settings.SPIDER_API_URL)
            hostname = parsed_url.hostname

            if not hostname or hostname == "host.docker.internal":
                return {}

            # 尝试解析 IP
            master_ip = socket.gethostbyname(hostname)
            logger.info("Resolved Master hostname '%s' to IP: %s", hostname, master_ip)
            return {hostname: master_ip}
        except (socket.gaierror, Exception) as exc:
            logger.warning("Failed to resolve Master hostname IP: %s", exc)
            return {}

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
        # 获取由 executor 传入的已经构建好的专有镜像 Tag
        image: str = task_payload.get("image_tag", task_payload.get("language", "python:3.11-slim"))

        container_name = f"spider-task-{task_id[:16]}"

        environment = {
            "TASK_ID": task_id,
            "SPIDER_API_URL": settings.SPIDER_API_URL,
            "PYTHONUNBUFFERED": "1",
        }

        logger.info(
            "Starting container for task %s | image=%s | network=%s | mem=%s",
            task_id, image, network, mem_limit,
        )

        # 动态解析 Master 宿主 IP 并注入
        extra_hosts = {"host.docker.internal": "host-gateway"}
        extra_hosts.update(self._resolve_master_host_mapping())

        try:
            container = self.client.containers.run(
                image=image,
                name=container_name,
                environment=environment,
                network=network,
                extra_hosts=extra_hosts,
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
                    name=container_name,
                    environment=environment,
                    network=network,
                    extra_hosts=extra_hosts,
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
            # 可能是跑完了或者已经被自动删除了，不必警告
            pass
        except APIError as exc:
            logger.error("Failed to stop container %s: %s", container_name_or_id, exc)

    def remove_container(self, container_name_or_id: str, force: bool = True) -> None:
        """强化清理：删除指定容器"""
        try:
            container = self.client.containers.get(container_name_or_id)
            container.remove(force=force)
            logger.info("Container %s removed explicitly.", container_name_or_id)
        except NotFound:
            pass
        except APIError as exc:
            # 如果因为 auto_remove=True 导致 Docker 已经在后台删除，会抛出 409 Conflict，直接忽略即可。
            if exc.response is not None and exc.response.status_code == 409:
                pass
            else:
                logger.warning("Failed to explicitly remove container %s: %s", container_name_or_id, exc)

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
