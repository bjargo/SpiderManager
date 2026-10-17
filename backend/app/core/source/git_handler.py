import os
from git import Git, Repo
from urllib.parse import urlsplit, urlunsplit, quote_plus
from config import settings
from .base import SourceHandler
import logging
logger = logging.getLogger(__name__)


def _redact_url(url: str) -> str:
    """Remove URL credentials and query parameters before logging Git remotes."""
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


class GitSourceHandler(SourceHandler):
    def fetch(self, url: str, dest_dir: str, **kwargs) -> None:
        """
        从 Git 仓库拉取代码，支持全局系统级凭证注入，绝不截取和泄漏 Token。
        """
        branch = kwargs.get("branch")
        safe_url = _redact_url(url)
        logger.info("Cloning git repository %s (branch: %s) to %s...", safe_url, branch or "default", dest_dir)

        # ── 1. 处理 HTTP/HTTPS 协议密码注入 ──
        auth_url = url
        if url.startswith("http://") or url.startswith("https://"):
            if settings.GIT_GLOBAL_USERNAME and settings.GIT_GLOBAL_PASSWORD:
                parsed = urlsplit(url)
                if not parsed.username:
                    # 安全地拼接带认证的 URL (注意密码需要 urlencode 防止特殊字符破环 URL)
                    safe_password = quote_plus(settings.GIT_GLOBAL_PASSWORD)
                    netloc = f"{settings.GIT_GLOBAL_USERNAME}:{safe_password}@{parsed.hostname}"
                    if parsed.port:
                        netloc = f"{netloc}:{parsed.port}"
                    parsed = parsed._replace(netloc=netloc)
                    auth_url = urlunsplit(parsed)

        # ── 2. 处理 SSH 协议密钥注入 ──
        env_vars = {}
        if settings.GIT_SSH_KEY_PATH and os.path.exists(settings.GIT_SSH_KEY_PATH):
            # 禁用 StrictHostKeyChecking 以防止未知主机卡死进城
            env_vars["GIT_SSH_COMMAND"] = f"ssh -i {settings.GIT_SSH_KEY_PATH} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

        repo = None
        try:
            if branch:
                repo = Repo.clone_from(auth_url, dest_dir, branch=branch, env=env_vars)
            else:
                repo = Repo.clone_from(auth_url, dest_dir, env=env_vars)
            logger.info("Successfully cloned Git repository %s.", safe_url)
        except Exception as e:
            logger.error("Failed to clone Git repository %s (%s)", safe_url, type(e).__name__)
            raise RuntimeError(
                f"Failed to clone Git repository {safe_url} ({type(e).__name__})"
            ) from None
        finally:
            if repo is not None:
                repo.close()

    def get_version_hash(self, local_path: str) -> str:
        """
        获取 Git 仓库的 HEAD commit ID
        """
        repo = None
        try:
            repo = Repo(local_path)
            commit_hash = repo.head.commit.hexsha
            logger.info(f"Git repository at {local_path} has version hash {commit_hash}.")
            return commit_hash
        except Exception as e:
            logger.error(f"Failed to get git version hash from {local_path}: {e}")
            raise e
        finally:
            if repo is not None:
                repo.close()

    def get_remote_fingerprint(self, url: str, **kwargs) -> str:
        """
        使用 git ls-remote 获取远程分支的最新 Commit ID。
        """
        branch = kwargs.get("branch") or "HEAD"
        safe_url = _redact_url(url)
        logger.info("Getting remote fingerprint for Git %s (branch: %s)...", safe_url, branch)

        # ── 1. 处理 HTTP/HTTPS 协议认证 ──
        auth_url = url
        if url.startswith("http://") or url.startswith("https://"):
            if settings.GIT_GLOBAL_USERNAME and settings.GIT_GLOBAL_PASSWORD:
                parsed = urlsplit(url)
                if not parsed.username:
                    safe_password = quote_plus(settings.GIT_GLOBAL_PASSWORD)
                    netloc = f"{settings.GIT_GLOBAL_USERNAME}:{safe_password}@{parsed.hostname}"
                    if parsed.port:
                        netloc = f"{netloc}:{parsed.port}"
                    parsed = parsed._replace(netloc=netloc)
                    auth_url = urlunsplit(parsed)

        # ── 2. 处理 SSH 协议认证 ──
        env_vars = {}
        if settings.GIT_SSH_KEY_PATH and os.path.exists(settings.GIT_SSH_KEY_PATH):
            env_vars["GIT_SSH_COMMAND"] = f"ssh -i {settings.GIT_SSH_KEY_PATH} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

        try:
            # 使用 Git().ls_remote 而非 Repo().ls_remote，因为不需要初始化本地仓库
            g = Git()
            # ls-remote 结果格式: "commit_id\tref_path"
            output = g.ls_remote(auth_url, branch, env=env_vars)
            if not output:
                # 尝试获取所有引用来验证连接
                refs = g.ls_remote(auth_url, env=env_vars).split('\n')
                if not refs or not refs[0]:
                    raise RuntimeError(f"Connection failed or no refs found at {safe_url}")
                # 如果没找到特定的 HEAD/branch，抛出异常以回退模式
                raise RuntimeError(f"Branch {branch} not found at {safe_url}")

            # 提取第一行的第一个字段
            remote_hash = output.split()[0]
            logger.info("Remote fingerprint for Git %s: %s", safe_url, remote_hash)
            return remote_hash
        except Exception as e:
            # 发生异常时，不要打印带密码的 auth_url
            logger.error("Failed to get Git remote fingerprint for %s (%s)", safe_url, type(e).__name__)
            raise RuntimeError(
                f"Failed to get Git remote fingerprint for {safe_url} ({type(e).__name__})"
            ) from None
