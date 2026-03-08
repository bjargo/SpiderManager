import os
import docker
import logging
from docker.errors import BuildError, DockerException
from app.core.container.runners import RunnerFactory


logger = logging.getLogger(__name__)

class ImageManager:
    """
    无状态镜像构建器。负责：
    1. 判断镜像是否存在，跳过重复构建。
    2. 基于模板生成 Dockerfile 和 .dockerignore。
    3. 调用 Docker Engine 进行构建。
    """
    
    def __init__(self):
        self._docker_client = None

    def _flatten_directory(self, local_path: str) -> str | None:
        """
        如果解压后存在单级顶层目录，将其内容移动到根部并删除该目录。
        防止 Dockerfile 中的 COPY . /app/ 导致多一层嵌套。
        返回被打平的目录名。
        """
        import shutil
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
    def docker_client(self):
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
        except docker.errors.ImageNotFound:
            logger.info(f"Image {image_tag} not found. Needs build.")
            return False
        except Exception as e:
            logger.error(f"Error checking image {image_tag}: {e}")
            return False

    def build_image(self, local_path: str, language: str, image_tag: str, entrypoint: str, build_args: dict = None) -> str:
        """
        核心构建逻辑：基于插件架构动态渲染 Dockerfile、.dockerignore 和启动 Docker build 进程。
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
            # 修正 entrypoint。如果 entrypoint 是 "python subdir/main.py"，
            # 在打平后应变为 "python main.py"。
            parts = entrypoint.split()
            new_parts = []
            prefix = f"{flattened_dir}/"
            for p in parts:
                if p.startswith(prefix):
                    new_parts.append(p[len(prefix):])
                elif p == flattened_dir:
                    new_parts.append(".")
                else:
                    new_parts.append(p)
            new_entrypoint = " ".join(new_parts)
            if new_entrypoint != entrypoint:
                logger.info(f"Adjusted entrypoint from '{entrypoint}' to '{new_entrypoint}' due to flattening.")
                entrypoint = new_entrypoint

        # 将 entrypoint 以及所有的 build_args 打包作为渲染上下文
        context_vars = {"entrypoint": entrypoint}
        context_vars.update(build_args)
        runner.prepare_context(local_path, context_vars)

        # 3. 调用 Docker Engine 构建镜像
        logger.info(f"Starting Docker build for image {image_tag}")
        dockerfile_path = os.path.join(local_path, "Dockerfile")
        if os.path.exists(dockerfile_path):
            with open(dockerfile_path, "r", encoding="utf-8") as f:
                logger.debug(f"Generated Dockerfile for {image_tag}:\n{f.read()}")
        else:
            logger.warning(f"Dockerfile not found in {local_path} for {image_tag}")
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
                if 'stream' in chunk:
                    logger.debug(chunk['stream'].strip())
                    
            logger.info(f"Successfully built image: {image_tag}")
            return image_tag
            
        except BuildError as e:
            # 提取完整的构建日志
            log_msgs = ""
            for msg in e.build_log:
                if 'stream' in msg:
                    log_msgs += msg['stream']
                elif 'error' in msg:
                    log_msgs += msg['error']
            logger.error(f"BuildError during {image_tag} build. Logs:\n{log_msgs}")
            raise RuntimeError(f"Failed to build Docker image {image_tag}. Error from daemon: {log_msgs}")
        except Exception as e:
            logger.error(f"Unexpected error building {image_tag}: {e}")
            raise e

    def prune_images(self, days_old: int = 7) -> dict:
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
