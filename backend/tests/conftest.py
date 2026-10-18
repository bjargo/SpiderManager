"""
测试公共配置。

异步测试统一使用 anyio 的 asyncio 后端（项目未引入 pytest-asyncio）。
"""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """固定使用 asyncio 后端。"""
    return "asyncio"
