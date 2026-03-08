import os
import git
import urllib.parse
from config import settings
from .base import SourceHandler
import logging
logger = logging.getLogger(__name__)

class GitSourceHandler(SourceHandler):
    def fetch(self, url: str, dest_dir: str, **kwargs) -> None:
        """
        从 Git 仓库拉取代码，支持全局系统级凭证注入，绝不截取和泄漏 Token。
        """
        branch = kwargs.get("branch")
        logger.info(f"Cloning git repository {url} (branch: {branch or 'default'}) to {dest_dir}...")
        
        # ── 1. 处理 HTTP/HTTPS 协议密码注入 ──
        auth_url = url
        if url.startswith("http://") or url.startswith("https://"):
            if settings.GIT_GLOBAL_USERNAME and settings.GIT_GLOBAL_PASSWORD:
                parsed = urllib.parse.urlparse(url)
                if not parsed.username:
                    # 安全地拼接带认证的 URL (注意密码需要 urlencode 防止特殊字符破环 URL)
                    safe_password = urllib.parse.quote_plus(settings.GIT_GLOBAL_PASSWORD)
                    netloc = f"{settings.GIT_GLOBAL_USERNAME}:{safe_password}@{parsed.hostname}"
                    if parsed.port:
                        netloc = f"{netloc}:{parsed.port}"
                    parsed = parsed._replace(netloc=netloc)
                    auth_url = urllib.parse.urlunparse(parsed)

        # ── 2. 处理 SSH 协议密钥注入 ──
        env_vars = {}
        if settings.GIT_SSH_KEY_PATH and os.path.exists(settings.GIT_SSH_KEY_PATH):
            # 禁用 StrictHostKeyChecking 以防止未知主机卡死进城
            env_vars["GIT_SSH_COMMAND"] = f"ssh -i {settings.GIT_SSH_KEY_PATH} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        
        try:
            if branch:
                repo = git.Repo.clone_from(auth_url, dest_dir, branch=branch, env=env_vars)
            else:
                repo = git.Repo.clone_from(auth_url, dest_dir, env=env_vars)
            logger.info(f"Successfully cloned git repository {url}.")
        except Exception as e:
            # 发生异常时，如果带有密码的认证信息，务必不要在系统日志中打印！只打印传入的原始免密 url。
            logger.error(f"Failed to clone git repository {url}: {e}")
            raise e

    def get_version_hash(self, local_path: str) -> str:
        """
        获取 Git 仓库的 HEAD commit ID
        """
        try:
            repo = git.Repo(local_path)
            commit_hash = repo.head.commit.hexsha
            logger.info(f"Git repository at {local_path} has version hash {commit_hash}.")
            return commit_hash
        except Exception as e:
            logger.error(f"Failed to get git version hash from {local_path}: {e}")
            raise e

    def get_remote_fingerprint(self, url: str, **kwargs) -> str:
        """
        使用 git ls-remote 获取远程分支的最新 Commit ID。
        """
        branch = kwargs.get("branch") or "HEAD"
        logger.info(f"Getting remote fingerprint for Git {url} (branch: {branch})...")
        
        # ── 1. 处理 HTTP/HTTPS 协议认证 ──
        auth_url = url
        if url.startswith("http://") or url.startswith("https://"):
            if settings.GIT_GLOBAL_USERNAME and settings.GIT_GLOBAL_PASSWORD:
                parsed = urllib.parse.urlparse(url)
                if not parsed.username:
                    safe_password = urllib.parse.quote_plus(settings.GIT_GLOBAL_PASSWORD)
                    netloc = f"{settings.GIT_GLOBAL_USERNAME}:{safe_password}@{parsed.hostname}"
                    if parsed.port:
                        netloc = f"{netloc}:{parsed.port}"
                    parsed = parsed._replace(netloc=netloc)
                    auth_url = urllib.parse.urlunparse(parsed)

        # ── 2. 处理 SSH 协议认证 ──
        env_vars = {}
        if settings.GIT_SSH_KEY_PATH and os.path.exists(settings.GIT_SSH_KEY_PATH):
            env_vars["GIT_SSH_COMMAND"] = f"ssh -i {settings.GIT_SSH_KEY_PATH} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        
        try:
            # 使用 git.cmd.Git().ls_remote 而非 Repo().ls_remote，因为不需要初始化本地仓库
            g = git.cmd.Git()
            # ls-remote 结果格式: "commit_id\tref_path"
            output = g.ls_remote(auth_url, branch, env=env_vars)
            if not output:
                # 尝试获取所有引用来验证连接
                refs = g.ls_remote(auth_url, env=env_vars).split('\n')
                if not refs or not refs[0]:
                    raise RuntimeError(f"Connection failed or no refs found at {url}")
                # 如果没找到特定的 HEAD/branch，抛出异常以回退模式
                raise RuntimeError(f"Branch {branch} not found at {url}")
            
            # 提取第一行的第一个字段
            remote_hash = output.split()[0]
            logger.info(f"Remote fingerprint for Git {url}: {remote_hash}")
            return remote_hash
        except Exception as e:
            # 发生异常时，不要打印带密码的 auth_url
            logger.error(f"Failed to get remote fingerprint for Git {url}: {e}")
            raise e
