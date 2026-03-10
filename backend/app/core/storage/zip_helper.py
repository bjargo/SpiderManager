"""
ZIP 文件操作辅助模块

集中管理 MinIO 上 ZIP 包的下载、解析、修改和上传操作，
供 spiders 模块的文件管理接口复用，消除重复代码。
"""
import io
import logging
import zipfile

from fastapi import HTTPException, status

from app.core.storage.minio_client import minio_manager

logger = logging.getLogger(__name__)

# ── 受保护的环境依赖文件集合，禁止在线修改 ──
_PROTECTED_FILES: set[str] = {
    "requirements.txt", "Pipfile", "Pipfile.lock",
    "pyproject.toml", "poetry.lock", "setup.py", "setup.cfg",
    "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
}

# 不在文件树中展示的路径模式
_SKIP_PATTERNS: set[str] = {"__pycache__", ".pyc", ".git", ".DS_Store", "__MACOSX"}


def check_protected_file(path: str) -> None:
    """
    检查目标路径是否命中受保护的依赖文件。

    :param path: ZIP 包内的文件路径
    :return: None
    :raises HTTPException: 403 — 如果路径是受保护文件
    """
    filename = path.rsplit("/", maxsplit=1)[-1] if "/" in path else path
    if filename in _PROTECTED_FILES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"禁止在线修改环境依赖文件: {filename}",
        )


def validate_file_path(path: str) -> None:
    """
    校验文件路径安全性，防止路径穿越攻击。

    :param path: ZIP 包内的文件路径
    :return: None
    :raises HTTPException: 400 — 如果路径为空、以 '/' 开头或包含 '..'
    """
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="文件路径不合法")


def _should_skip(path: str) -> bool:
    """
    判断 ZIP 内路径是否需要跳过（如 __pycache__、.git 等）。

    :param path: ZIP 包内的文件路径
    :return: True 表示应跳过该路径
    """
    parts = path.split("/")
    return any(p for p in parts if any(skip in p for skip in _SKIP_PATTERNS))


def download_zip_bytes(source_url: str, spider_id: int) -> bytes:
    """
    从 MinIO 下载 ZIP 文件的原始字节。

    :param source_url: MinIO 中的对象路径
    :param spider_id: 爬虫 ID，用于日志记录
    :return: ZIP 文件的完整字节内容
    :raises HTTPException: 500 — 下载失败时
    """
    try:
        return minio_manager.download_object(source_url)
    except RuntimeError as e:
        logger.error(f"Failed to download ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="下载 ZIP 文件失败")


def list_files(zip_bytes: bytes) -> list[str]:
    """
    列出 ZIP 包内所有非目录、非跳过的文件路径。

    :param zip_bytes: ZIP 文件的字节内容
    :return: 文件路径列表
    :raises HTTPException: 400 — ZIP 格式损坏时
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            return [
                info.filename
                for info in zf.infolist()
                if not info.is_dir() and not _should_skip(info.filename)
            ]
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")


def read_file(zip_bytes: bytes, path: str) -> str:
    """
    读取 ZIP 包内指定文件的文本内容。

    :param zip_bytes: ZIP 文件的字节内容
    :param path: 要读取的文件在 ZIP 内的路径
    :return: 文件文本内容（优先 UTF-8，回退 Latin-1）
    :raises HTTPException: 404 — 文件不存在；400 — ZIP 格式损坏
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            if path not in zf.namelist():
                raise HTTPException(status_code=404, detail=f"文件 {path} 不存在于 ZIP 包中")
            raw = zf.read(path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def update_file(zip_bytes: bytes, path: str, content: str) -> bytes:
    """
    替换 ZIP 包内指定文件的内容，返回新的 ZIP 字节。

    :param zip_bytes: 原始 ZIP 文件的字节内容
    :param path: 要替换的文件在 ZIP 内的路径
    :param content: 新的文件内容（文本）
    :return: 修改后的 ZIP 文件字节内容
    :raises HTTPException: 400 — ZIP 格式损坏
    """
    try:
        old_zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zf:
            for item in old_zf.infolist():
                if item.filename == path:
                    new_zf.writestr(item, content.encode("utf-8"))
                else:
                    new_zf.writestr(item, old_zf.read(item.filename))
        old_zf.close()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    return buf.getvalue()


def add_file(zip_bytes: bytes, path: str, content: str) -> bytes:
    """
    向 ZIP 包中新增一个文件，返回新的 ZIP 字节。

    :param zip_bytes: 原始 ZIP 文件的字节内容
    :param path: 新文件在 ZIP 内的路径
    :param content: 新文件内容（文本）
    :return: 修改后的 ZIP 文件字节内容
    :raises HTTPException: 409 — 文件已存在；400 — ZIP 格式损坏
    """
    try:
        old_zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        if path in old_zf.namelist():
            old_zf.close()
            raise HTTPException(status_code=409, detail=f"文件 {path} 已存在")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zf:
            for item in old_zf.infolist():
                new_zf.writestr(item, old_zf.read(item.filename))
            new_zf.writestr(path, content.encode("utf-8"))
        old_zf.close()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    return buf.getvalue()


def delete_file(zip_bytes: bytes, path: str) -> bytes:
    """
    从 ZIP 包中删除指定文件，返回新的 ZIP 字节。

    :param zip_bytes: 原始 ZIP 文件的字节内容
    :param path: 要删除的文件在 ZIP 内的路径
    :return: 修改后的 ZIP 文件字节内容
    :raises HTTPException: 404 — 文件不存在；400 — ZIP 格式损坏
    """
    try:
        old_zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
        if path not in old_zf.namelist():
            old_zf.close()
            raise HTTPException(status_code=404, detail=f"文件 {path} 不存在于 ZIP 包中")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zf:
            for item in old_zf.infolist():
                if item.filename != path:
                    new_zf.writestr(item, old_zf.read(item.filename))
        old_zf.close()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 文件格式损坏")

    return buf.getvalue()


def upload_zip_bytes(source_url: str, data: bytes, spider_id: int) -> None:
    """
    将修改后的 ZIP 字节上传（覆盖）到 MinIO。

    :param source_url: MinIO 中的对象路径
    :param data: 要上传的 ZIP 字节内容
    :param spider_id: 爬虫 ID，用于日志记录
    :return: None
    :raises HTTPException: 500 — 上传失败时
    """
    try:
        minio_manager.upload_bytes(source_url, data)
    except RuntimeError as e:
        logger.error(f"Failed to upload modified ZIP for spider {spider_id}: {e}")
        raise HTTPException(status_code=500, detail="上传修改后的 ZIP 失败")
