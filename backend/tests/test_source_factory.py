import os
import sys
import pytest

# Add backend directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.source.factory import SourceFactory
from app.core.source.git_handler import GitSourceHandler
from app.core.source.git_handler import _redact_url
from app.core.source.minio_handler import MinioSourceHandler


def test_source_factory_git():
    """测试 Git 类型的 Handler 生成 (大小写不敏感)"""
    handler_lower = SourceFactory.get_handler("git")
    assert isinstance(handler_lower, GitSourceHandler)

    handler_upper = SourceFactory.get_handler("GIT")
    assert isinstance(handler_upper, GitSourceHandler)


def test_source_factory_minio():
    """测试 MinIO/file/local 类型的 Handler 生成"""
    for src_type in ["minio", "MINIO", "file", "local"]:
        handler = SourceFactory.get_handler(src_type)
        assert isinstance(handler, MinioSourceHandler)


def test_source_factory_unsupported():
    """测试不支持的源类型时抛出 ValueError"""
    with pytest.raises(ValueError, match="Unsupported source type"):
        SourceFactory.get_handler("svn")

    with pytest.raises(ValueError, match="Unsupported source type"):
        SourceFactory.get_handler("unknown")


def test_git_remote_redacts_url_credentials():
    assert _redact_url(
        "https://worker:token-value@example.com/group/repo.git?access_token=query-secret"
    ) == "https://example.com/group/repo.git"
