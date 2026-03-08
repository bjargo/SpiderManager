from abc import ABC, abstractmethod

class SourceHandler(ABC):
    @abstractmethod
    def fetch(self, url: str, dest_dir: str, **kwargs) -> None:
        """
        拉取代码到指定目录
        """
        pass

    @abstractmethod
    def get_version_hash(self, local_path: str) -> str:
        """
        获取代码的版本哈希
        """
        pass

    @abstractmethod
    def get_remote_fingerprint(self, url: str, **kwargs) -> str:
        """
        获取远程源码的指纹（如 ETag 或 CommitID），用于预判镜像是否存在。
        """
        pass
