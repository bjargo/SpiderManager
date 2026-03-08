from .base import SourceHandler
from .git_handler import GitSourceHandler
from .minio_handler import MinioSourceHandler

class SourceFactory:
    @staticmethod
    def get_handler(source_type: str) -> SourceHandler:
        """
        获取对应的 Source Handler
        """
        if source_type.lower() == "git":
            return GitSourceHandler()
        elif source_type.lower() in ["minio", "file", "local"]:
            return MinioSourceHandler()
        else:
            raise ValueError(f"Unsupported source type: {source_type}")
