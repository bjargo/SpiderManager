import os
import sys
import logging
import traceback
from logging.handlers import TimedRotatingFileHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

_app_logger = logging.getLogger("spider_manager")


def setup_logging() -> None:
    """
    尽早完成日志系统初始化。
    优化点：增加了对第三方库日志级别的控制，防止 SQLAlchemy 等库打印过细的内部逻辑。
    """
    os.makedirs("log", exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # 统一日志格式
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    # 1. 屏蔽/提升第三方库的日志级别，防止它们污染日志
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.dialects.postgresql").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    # 构建 Handlers
    info_h = _build_file_handler("log/log.log", logging.INFO, fmt)
    error_h = _build_file_handler("log/error.log", logging.ERROR, fmt)

    console_h = logging.StreamHandler()
    console_h.setLevel(logging.INFO)
    console_h.setFormatter(fmt)

    root.handlers.clear()
    root.addHandler(info_h)
    root.addHandler(error_h)
    root.addHandler(console_h)

    # 捕获进程级未处理异常
    def _handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        
        # 进程级异常同样进行精简处理
        _app_logger.error("Uncaught process-level exception", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _handle_exception


def bind_app(app: "FastAPI") -> None:
    """
    向 FastAPI 实例注册全局请求级异常处理器。
    优化点：过滤掉 site-packages 路径，只记录业务代码堆栈。
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        # --- 核心改进：堆栈过滤逻辑 ---
        tb = exc.__traceback__
        # 提取堆栈帧
        frames = traceback.extract_tb(tb)
        # 只保留不包含 'site-packages' 的帧（即你自己的项目代码）
        clean_frames = [f for f in frames if "site-packages" not in f.filename]
        
        if clean_frames:
            # 格式化过滤后的堆栈
            readable_traceback = "".join(traceback.format_list(clean_frames))
        else:
            # 如果堆栈全部来自第三方库，则显示最后两行以供参考
            readable_traceback = "".join(traceback.format_list(frames[-2:]))

        # 记录精简后的日志
        _app_logger.error(
            "Unhandled exception: %s %s\n"
            "Business Traceback:\n%s"
            "Error Type: %s\n"
            "Error Detail: %s",
            request.method, 
            request.url.path, 
            readable_traceback,
            type(exc).__name__,
            str(exc)
        )
        # ----------------------------

        return JSONResponse(
            status_code=500,
            content={
                "code": 500, 
                "message": "Internal Server Error", 
                "detail": str(exc) if os.getenv("DEBUG") else "Please contact admin"
            },
        )


def _build_file_handler(path: str, level: int, fmt: logging.Formatter) -> TimedRotatingFileHandler:
    h = TimedRotatingFileHandler(
        filename=path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    h.setLevel(level)
    h.setFormatter(fmt)
    return h