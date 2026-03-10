import contextlib

import logging
logger = logging.getLogger(__name__)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator, metrics

from app.core.logger import setup_logging, bind_app
setup_logging()

from app.api.monitor.router import router as monitor_router
from app.api.logs.router import router as logs_router
from app.api.messages.router import router as messages_router
from app.api.nodes.router import router as nodes_router
from app.api.tasks.router import router as tasks_router
from app.api.projects.router import router as projects_router
from app.api.spiders.router import router as spiders_router
from app.api.users.router import router as users_router
from app.api.dashboard.router import router as dashboard_router
from app.api.admin.router import router as admin_router
from app.core.redis import redis_manager
from app.core.scheduler import start_scheduler, shutdown_scheduler
from config import settings
from app.core.startup import clean_orphaned_tasks
from app.core.timezone import now
from app.core.storage.minio_client import minio_manager
from app.worker.heartbeat import start_heartbeat_task
from app.db.database import create_tables
from app.db.init_data import init_superuser




@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_manager.init_pool()
    create_tables()

    # 初始化默认超级管理员
    if settings.FIRST_SUPERUSER_EMAIL and settings.FIRST_SUPERUSER_PASSWORD:
        await init_superuser(settings.FIRST_SUPERUSER_EMAIL, settings.FIRST_SUPERUSER_PASSWORD)

    # 启动时清理孤儿任务
    await clean_orphaned_tasks()

    node_role = getattr(settings, "NODE_ROLE", "master")
    heartbeat_task = None
    task_listener_task = None
    data_reducer_task = None

    if node_role != "worker":
        start_scheduler()
        minio_manager.init_client()
        heartbeat_task = await start_heartbeat_task()

    # master 和 worker 都可以消费任务队列
    from app.worker.executor import start_task_listener
    task_listener_task = await start_task_listener()

    # Data Reducer：消费爬虫采集数据队列，批量入库 + 实时分发
    from app.worker.data_reducer import start_data_reducer
    data_reducer_task = await start_data_reducer()

    yield

    if data_reducer_task:
        data_reducer_task.cancel()
    if task_listener_task:
        task_listener_task.cancel()
    if node_role != "worker":
        if heartbeat_task:
            heartbeat_task.cancel()
        shutdown_scheduler()
    await redis_manager.close_pool()

app = FastAPI(title="SpiderManage API", lifespan=lifespan, redirect_slashes=False)
bind_app(app)  # 注册全局请求异常处理器，将 500 错误写入 error.log

def logging_metric():
    """自定义指标：不仅交由 Prometheus 采集，还将响应时间直接写入 error.log（或其他 logger 配置的输出）"""
    perf_logger = logger

    def instrumentation(info: metrics.Info) -> None:
        # info.modified_duration 即为接口消耗时间 (秒)
        perf_logger.info(
            f"API PERF: {info.request.method} {info.request.url.path} "
            f"| Status: {info.response.status_code} "
            f"| Time: {info.modified_duration:.4f}s"
        )
    return instrumentation

instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=False,
    should_instrument_requests_inprogress=True,
    inprogress_name="inprogress",
    inprogress_labels=True,
)
instrumentator.add(logging_metric())
instrumentator.instrument(app).expose(app, endpoint="/metrics")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.core.middleware import AuditContextMiddleware
app.add_middleware(AuditContextMiddleware)


# ---- 业务路由 ----
app.include_router(monitor_router, prefix="/api/monitor", tags=["监控"])
app.include_router(logs_router, prefix="/api/logs", tags=["日志"])
app.include_router(messages_router, prefix="/api/messages", tags=["消息"])
app.include_router(nodes_router, prefix="/api/nodes", tags=["节点管理"])
app.include_router(projects_router, prefix="/api/projects", tags=["项目管理"])
app.include_router(spiders_router, prefix="/api/spiders", tags=["爬虫管理"])
app.include_router(tasks_router, prefix="/api/tasks", tags=["任务管理"])
app.include_router(users_router, prefix="/api/users", tags=["用户"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["大盘"])
app.include_router(admin_router, prefix="/api/admin", tags=["管理员"])

# 独立注册 WebSocket 路由，避免 Router 前缀干扰
from app.api.tasks.websocket import websocket_task_logs, websocket_task_data
app.add_api_websocket_route("/ws-logs/{task_id}", websocket_task_logs)
app.add_api_websocket_route("/ws-data/{task_id}", websocket_task_data)

@app.get("/")
def read_root():
    return {"message": "Welcome to SpiderManage API"}
