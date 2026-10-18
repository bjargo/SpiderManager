import io
import os
import json
import docker
import logging
import tarfile
from docker.constants import IS_WINDOWS_PLATFORM
from docker.errors import APIError, BuildError, DockerException, ImageNotFound
from docker.utils.build import exclude_paths
from requests.exceptions import RequestException
from app.core.container.runners import RunnerFactory
from app.core.run_config import build_exec_argv, flatten_entrypoint


logger = logging.getLogger(__name__)


class ImageBuildError(RuntimeError):
    """
    镜像构建失败异常。

    携带 Docker 守护进程返回的完整构建日志（如 pip 安装报错），
    供上层 Worker 将该日志持久化到任务日志中，方便排查代码依赖问题。
    """

    def __init__(self, image_tag: str, build_log: str) -> None:
        self.image_tag = image_tag
        self.build_log = build_log
        super().__init__(f"Failed to build Docker image {image_tag}")


class BuildKitUnavailable(RuntimeError):
    """
    内部信号：BuildKit 构建失败或不可用，需要回退到经典 builder。

    BuildKit 的错误输出（尤其是 RUN 阶段失败）大多以 protobuf trace 形式返回，
    可读性差；回退到经典 builder 后可以拿到结构化的构建日志并持久化。
    """

    def __init__(self, message: str, build_log: str = "") -> None:
        self.build_log = build_log
        super().__init__(message)


def _extract_build_log(build_log: list | None) -> str:
    """从 docker.errors.BuildError.build_log 中提取可读的纯文本构建日志。"""
    if not build_log:
        return ""
    log_msgs = ""
    for msg in build_log:
        if isinstance(msg, dict):
            if "stream" in msg and isinstance(msg["stream"], str):
                log_msgs += msg["stream"]
            elif "error" in msg and isinstance(msg["error"], str):
                log_msgs += msg["error"] + "\n"
    return log_msgs.strip()


def _redact_build_arg_values(message: str, build_args: dict[str, str]) -> str:
    for value in build_args.values():
        if value:
            message = message.replace(value, "[REDACTED]")
    return message


class ImageManager:
    """
    无状态镜像构建器。负责：
    1. 判断镜像是否存在，跳过重复构建。
    2. 基于模板生成 Dockerfile 和 .dockerignore。
    3. 调用 Docker Engine 进行构建。
    """

    def __init__(self) -> None:
        self._docker_client: docker.DockerClient | None = None

    def _flatten_directory(self, local_path: str) -> str | None:
        """
        如果解压后存在单级顶层目录，将其内容移动到根部并删除该目录。
        防止 Dockerfile 中的 COPY . /app/ 导致多一层嵌套。
        返回被打平的目录名。
        """
        import shutil
        if not os.path.exists(local_path) or not os.path.isdir(local_path):
            return None
        all_entries = [e for e in os.listdir(local_path) if not e.startswith('.')]
        if len(all_entries) == 1:
            flattened_name = all_entries[0]
            top_dir = os.path.join(local_path, flattened_name)
            if os.path.isdir(top_dir):
                logger.info(f"Flattening directory: Moving contents of {flattened_name} to {local_path}")
                for item in os.listdir(top_dir):
                    s = os.path.join(top_dir, item)
                    d = os.path.join(local_path, item)
                    if os.path.exists(d):
                        if os.path.isdir(d):
                            shutil.rmtree(d)
                        else:
                            os.remove(d)
                    shutil.move(s, d)
                os.rmdir(top_dir)
                return flattened_name
        return None

    @property
    def docker_client(self) -> docker.DockerClient:
        if self._docker_client is None:
            try:
                self._docker_client = docker.from_env()
            except DockerException as e:
                logger.error(f"Failed to initialize Docker client: {e}")
                raise RuntimeError(f"Docker is not running or accessible: {e}")
        return self._docker_client

    def check_image_exists(self, image_tag: str) -> bool:
        """检查所需镜像是否在本地已存在"""
        try:
            self.docker_client.images.get(image_tag)
            logger.info(f"Image {image_tag} already exists. Skipping build.")
            return True
        except ImageNotFound:
            logger.info(f"Image {image_tag} not found. Needs build.")
            return False
        except Exception as e:
            logger.error(f"Error checking image {image_tag}: {e}")
            return False

    def build_image(
        self,
        local_path: str,
        language: str,
        image_tag: str,
        entrypoint: str,
        args: list[str] | None = None,
        build_args: dict[str, str] | None = None,
    ) -> str:
        """
        核心构建逻辑：基于插件架构动态渲染 Dockerfile、.dockerignore 和启动 Docker build 进程。

        入口文件与参数以 argv 数组的形式写入 Dockerfile 的 exec 形式 CMD，
        不再拼接 Shell 命令字符串。
        Returns:
            str: The image tag built.
        """
        if build_args is None:
            build_args = {}

        # 1. 检查是否存在，如果存在直接返回
        if self.check_image_exists(image_tag):
            return image_tag

        # 2. 准备 Dockerfile 和上下文环境
        runner = RunnerFactory.get_runner(language)

        # 强制打平目录结构
        flattened_dir = self._flatten_directory(local_path)
        if flattened_dir and entrypoint:
            flattened_entrypoint = flatten_entrypoint(entrypoint, flattened_dir)
            if flattened_entrypoint != entrypoint:
                logger.info(
                    "Adjusted entrypoint from '%s' to '%s' due to flattening.",
                    entrypoint,
                    flattened_entrypoint,
                )
                entrypoint = flattened_entrypoint

        # Only non-secret Dockerfile options belong in the rendered build context.
        # Build arguments are passed separately to the Docker API below.
        context_vars = {
            "entrypoint": entrypoint,
            "runtime_argv": json.dumps(build_exec_argv(language, entrypoint, args or [])),
            "buildkit_enabled": True,
        }
        runner.prepare_context(local_path, context_vars)

        # 3. 调用 Docker Engine 构建镜像
        dockerfile_path = os.path.join(local_path, "Dockerfile")
        if os.path.exists(dockerfile_path):
            logger.debug("Generated Dockerfile for %s at %s", image_tag, dockerfile_path)
        else:
            logger.warning(f"Dockerfile not found in {local_path} for {image_tag}")

        # 3.1 优先使用 BuildKit，以便模板中的 RUN --mount=type=cache 生效
        logger.info(f"Starting BuildKit build for image {image_tag}")
        try:
            self._build_with_buildkit(local_path, image_tag, build_args)
            logger.info(f"Successfully built image: {image_tag}")
            return image_tag
        except BuildKitUnavailable as e:
            logger.warning(
                f"BuildKit build failed for {image_tag}: {e}. "
                "Falling back to legacy builder without cache mounts."
            )

        # 3.2 回退经典 builder：重新渲染不包含 cache mount 的 Dockerfile
        context_vars["buildkit_enabled"] = False
        runner.prepare_context(local_path, context_vars)

        logger.info(f"Starting legacy Docker build for image {image_tag}")
        self._build_with_legacy(local_path, image_tag, build_args)
        logger.info(f"Successfully built image: {image_tag}")
        return image_tag

    def _create_build_context(self, local_path: str) -> io.BytesIO:
        """
        生成 Docker 构建上下文 tar。

        不复用 docker-py 的 `utils.build.tar`：Python 3.8+ 的 tarfile 默认使用 PAX
        格式，会为带亚秒级 mtime 的文件写入 `././@PaxHeader` 扩展头；Docker BuildKit
        会把这个扩展头误当作 Dockerfile 内容，报
        `unknown instruction: ././@PaxHeader...`。这里强制 GNU 格式规避该问题，
        同时复用 docker-py 的 `.dockerignore` 匹配逻辑，保持过滤语义一致。
        """
        root = os.path.abspath(local_path)
        patterns = [".dockerignore"]
        dockerignore_path = os.path.join(root, ".dockerignore")
        if os.path.exists(dockerignore_path):
            with open(dockerignore_path, "r", encoding="utf-8") as f:
                patterns.extend(filter(
                    lambda line: line != "" and line[0] != "#",
                    [line.strip() for line in f.read().splitlines()],
                ))

        # exclude_paths 会强制包含 Dockerfile
        files = sorted(exclude_paths(root, patterns))

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for path in files:
                full_path = os.path.join(root, path)
                arcname = path.replace(os.path.sep, "/")
                info = tar.gettarinfo(full_path, arcname=arcname)
                if info is None:
                    # 遇到 socket 等无法归档的文件时安全跳过
                    continue

                # 兼容 Python http://bugs.python.org/issue32713 的时间戳溢出问题
                if info.mtime < 0 or info.mtime > 8**11 - 1:
                    info.mtime = int(info.mtime)

                if IS_WINDOWS_PLATFORM:
                    # Windows 不保留可执行位，默认让文件与目录可执行
                    info.mode = info.mode & 0o755 | 0o111

                if info.isfile():
                    try:
                        with open(full_path, "rb") as f:
                            tar.addfile(info, f)
                    except OSError as oe:
                        raise RuntimeError(
                            f"Can not read file in build context: {full_path}"
                        ) from oe
                else:
                    # 目录、FIFO、符号链接等无需读取内容
                    tar.addfile(info, None)

        buffer.seek(0)
        return buffer

    def _build_with_buildkit(
        self, local_path: str, image_tag: str, build_args: dict[str, str]
    ) -> None:
        """
        通过低层 API (`POST /build?version=2`) 触发 BuildKit 构建。

        docker-py 7.x 的 `images.build` 无法开启 BuildKit，因此这里直接复用其内部
        的上下文打包与流解析逻辑，仅在请求参数中补充 `version=2`。
        """
        api = self.docker_client.api
        context = self._create_build_context(local_path)

        params = {
            "t": image_tag,
            "version": "2",  # 启用 BuildKit，RUN --mount=type=cache 依赖该参数
            "rm": "1",
            "forcerm": "1",
            "dockerfile": "Dockerfile",
        }
        if build_args:
            params["buildargs"] = json.dumps(build_args)

        try:
            response = api._post(
                api._url("/build"),
                data=context,
                params=params,
                headers={"Content-Type": "application/tar"},
                stream=True,
                # 构建可能持续数十秒到数分钟（拉依赖），且期间可能长时间无输出；
                # 关闭读超时，避免 socket Read timed out 导致误判为构建失败。
                timeout=None,
            )
        except (APIError, DockerException, RequestException) as e:
            raise BuildKitUnavailable(
                _redact_build_arg_values(f"BuildKit request failed: {e}", build_args)
            ) from e
        finally:
            context.close()

        log_lines: list[str] = []
        error_message = ""
        try:
            # 真实响应非 2xx 时直接回退；用 response.ok 避免测试中的 mock 比较运算报错
            if not response.ok:
                raise BuildKitUnavailable(
                    f"BuildKit returned HTTP {getattr(response, 'status_code', 'unknown')}"
                )

            for chunk in api._stream_helper(response, decode=True):
                if not isinstance(chunk, dict):
                    continue
                stream_text = chunk.get("stream")
                if isinstance(stream_text, str):
                    log_lines.append(_redact_build_arg_values(stream_text, build_args))
                if chunk.get("error"):
                    error_message = _redact_build_arg_values(str(chunk["error"]), build_args)
                error_detail = chunk.get("errorDetail")
                if isinstance(error_detail, dict) and error_detail.get("message"):
                    error_message = _redact_build_arg_values(error_detail["message"], build_args)
        except BuildKitUnavailable:
            raise
        except Exception as e:
            raise BuildKitUnavailable(
                _redact_build_arg_values(f"Failed to read BuildKit output: {e}", build_args),
                "".join(log_lines).strip(),
            ) from e
        finally:
            response.close()

        if error_message:
            raise BuildKitUnavailable(error_message, "".join(log_lines).strip())

        for line in log_lines:
            if line.strip():
                logger.debug(_redact_build_arg_values(line.strip(), build_args))

    def _build_with_legacy(
        self, local_path: str, image_tag: str, build_args: dict[str, str]
    ) -> None:
        """使用 docker-py 的经典 builder 构建（不支持 cache mount，但日志可读）。"""
        try:
            image, build_logs = self.docker_client.images.build(
                path=local_path,
                tag=image_tag,
                buildargs=build_args, # 透传给 Docker 引擎
                rm=True,  # 构建成功后删除中间容器
                forcerm=True # 出错也删除
            )

            # 记录构建日志，方便调试
            for chunk in build_logs:
                if isinstance(chunk, dict) and "stream" in chunk:
                    stream_text = chunk["stream"]
                    if isinstance(stream_text, str):
                        logger.debug(_redact_build_arg_values(stream_text.strip(), build_args))

        except BuildError as e:
            # 提取完整的构建日志（如 pip / npm 安装报错）并封装进异常，交由上层持久化
            log_msgs = _redact_build_arg_values(_extract_build_log(e.build_log), build_args)
            logger.error(f"BuildError during {image_tag} build. Logs:\n{log_msgs}")
            raise ImageBuildError(image_tag, log_msgs) from e
        except Exception as e:
            safe_error = _redact_build_arg_values(str(e), build_args)
            logger.error("Unexpected error building %s: %s", image_tag, safe_error)
            raise RuntimeError(safe_error) from e

    def prune_images(self, days_old: int = 7) -> dict[str, int]:
        """
        清理不再使用的游离镜像 (dangling) 和超出时间阈值的失效爬虫专用镜像。
        返回清理统计信息字典。
        """
        import time
        from datetime import datetime, timezone

        pruned_stats = {"dangling_deleted": 0, "dangling_space_reclaimed": 0, "spider_images_deleted": 0}

        # 1. 自动清理没有任何 tag 的游离层 (Dangling images)
        try:
            dangling_result = self.docker_client.images.prune(filters={'dangling': True})
            if dangling_result and isinstance(dangling_result, dict):
                pruned_stats["dangling_deleted"] = len(dangling_result.get('ImagesDeleted') or [])
                pruned_stats["dangling_space_reclaimed"] = dangling_result.get('SpaceReclaimed') or 0
                logger.info(f"Pruned dangling images: {dangling_result}")
        except Exception as e:
            logger.error(f"Error pruning dangling images: {e}")

        # 2. 清理 `spider-` 前缀且超时的镜像
        try:
            # 获取所有以 spider- 开头的镜像
            spider_images = self.docker_client.images.list(filters={'reference': 'spider-*'})
            now = datetime.now(timezone.utc)

            for img in spider_images:
                # Docker API 返回的时间格式，如 "2023-10-27T10:15:30.123456789Z"
                # docker-py 的 attrs['Created'] 可能会返回带纳秒时间的字符串，需要处理
                created_str = img.attrs.get('Created', '')
                try:
                    # 简化时间解析，忽略微秒和纳秒
                    time_part = created_str.split('.')[0]
                    if not time_part.endswith('Z'):
                        time_part = time_part[:19] # 如果遇到奇怪格式强行截断为 YYYY-MM-DDTHH:MM:SS
                    created_date = datetime.strptime(time_part, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                    delta = now - created_date

                    if delta.days >= days_old:
                        # 删除过期镜像
                        logger.info(f"Removing old spider image: {img.tags}")
                        try:
                            # 强制删除
                            self.docker_client.images.remove(image=img.id, force=True)
                            pruned_stats["spider_images_deleted"] += 1
                        except Exception as rm_err:
                            logger.error(f"Failed to remove old image {img.tags}: {rm_err}")
                except Exception as parse_err:
                    logger.warning(f"Failed to parse creation date for image {img.tags} ({created_str}): {parse_err}")

        except Exception as e:
            logger.error(f"Error checking and pruning old spider images: {e}")

        logger.info(f"Image pruning finished. Stats: {pruned_stats}")
        return pruned_stats

image_manager = ImageManager()
