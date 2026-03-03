import os
import uuid
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_persistent_node_id() -> str:
    env_id = os.getenv("NODE_ID")
    if env_id:
        return env_id

    id_file = ".node_id"
    if os.path.exists(id_file):
        try:
            with open(id_file, "r") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass

    new_id = str(uuid.uuid4())
    try:
        with open(id_file, "w") as f:
            f.write(new_id)
    except Exception:
        pass
    return new_id


class Settings(BaseSettings):
    """
    全局配置管理类
    自动从环境变量或 .env 文件中加载配置参数
    """
    PROJECT_NAME: str = "SpiderManage"
    API_V1_STR: str = "/api/v1"

    # ── 数据库配置 (PostgreSQL) ──
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgrespassword@localhost:5432/spidermanage"

    # ── 缓存与队列配置 (Redis) ──
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 100
    REDIS_SOCKET_TIMEOUT: float = 15.0

    # ── MINIO / 对象存储配置 ──
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str = "minioadmin"
    MINIO_BUCKET_NAME: str = "spidermanage-projects"

    # ── 安全配置 ──
    SECRET_KEY: str = "YOUR_SUPER_SECRET_KEY_HERE"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # ── 节点识别配置 ──
    NODE_ROLE: str = "master"  # 可选: master, worker
    NODE_ID: str = _get_persistent_node_id()

    # ── 心跳配置 ──
    HEARTBEAT_INTERVAL: int = 5       # 心跳发送间隔（秒）
    HEARTBEAT_TTL: int = 15           # 心跳 Redis 键过期时间（秒）

    # ── 调度器配置 ──
    SCHEDULER_THREAD_POOL_SIZE: int = 20
    SCHEDULER_MAX_INSTANCES: int = 3

    # ── 任务配置 ──
    TASK_STATUS_EXPIRE_SECONDS: int = 7 * 24 * 3600  # 任务状态在 Redis 中的过期时间

    # ── Redis 队列键名 ──
    PUBLIC_QUEUE_KEY: str = "task:queue:public"
    NODE_QUEUE_PREFIX: str = "task:queue:"

    # ── Worker 日志缓冲 ──
    LOG_FLUSH_SIZE: int = 20          # 每 N 行 flush 一次
    LOG_FLUSH_INTERVAL: float = 2.0   # 最多 M 秒 flush 一次

    # ── Docker 容器资源限制 (DooD) ──
    DOCKER_MEM_LIMIT: str = "512m"
    DOCKER_CPU_PERIOD: int = 100_000   # CFS 调度器周期 (µs)
    DOCKER_CPU_QUOTA: int = 100_000    # 等效 1 核 CPU
    DOCKER_NETWORK: str = "spidermanage_net"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # 忽略环境中未在 Settings 中声明的变量
    )


# 实例化全局配置对象，供其他模块依赖注入使用
settings = Settings()
