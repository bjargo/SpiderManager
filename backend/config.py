import os
import uuid
from functools import cached_property

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
    # 各字段可独立通过环境变量覆盖，由 DATABASE_URL 属性自动拼接完整连接字符串
    # docker-compose.yml 中 DB_HOST 可直接引用 YAML 锚点 *host-db
    DB_HOST: str = "localhost"            # 数据库主机，Docker 模式覆盖为服务名 "db"
    DB_PORT: int = 5432                   # 数据库端口
    DB_USER: str = "postgres"             # 数据库用户名
    DB_PASSWORD: str = "postgrespassword" # 数据库密码
    DB_NAME: str = "spidermanage"         # 数据库名称
    SPIDER_DB_NAME: str = "Spider"        # 专用爬虫数据库名称

    # ── 缓存与队列配置 (Redis) ──
    # 各字段可独立通过环境变量覆盖，由 REDIS_URL 属性自动拼接完整连接字符串
    # docker-compose.yml 中 REDIS_HOST 可直接引用 YAML 锚点 *host-redis
    REDIS_HOST: str = "localhost"  # Redis 主机，Docker 模式覆盖为服务名 "redis"
    REDIS_PORT: int = 6379         # Redis 端口
    REDIS_DB: int = 0              # Redis 数据库编号
    REDIS_MAX_CONNECTIONS: int = 100
    REDIS_SOCKET_TIMEOUT: float = 15.0

    @cached_property
    def DATABASE_URL(self) -> str:
        """从独立字段拼接 PostgreSQL 异步连接字符串"""
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @cached_property
    def SPIDER_DATABASE_URL(self) -> str:
        """从独立字段拼接爬虫数据专属 PostgreSQL 异步连接字符串"""
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.SPIDER_DB_NAME}"
        )

    @cached_property
    def REDIS_URL(self) -> str:
        """从独立字段拼接 Redis 连接字符串"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ── MINIO / 对象存储配置 ──
    MINIO_ENDPOINT: str = "localhost:9000"          # 宿主机/后端访问 MinIO 的地址
    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str = "minioadmin"
    MINIO_BUCKET_NAME: str = "spidermanage-projects"
    # 爬虫容器内访问 MinIO 的地址（容器内 localhost 指容器自身，不是宿主机）
    # 开发模式(本地uvicorn)：设为 host.docker.internal:9000（Docker Desktop 宿主机别名）
    # Docker Compose 生产模式：通过环境变量覆盖为 minio:9000（Docker 服务名）
    MINIO_CONTAINER_ENDPOINT: str = "host.docker.internal:9000"

    # ── Git 全局私有仓库鉴权配置 ──
    GIT_GLOBAL_USERNAME: str | None = None
    GIT_GLOBAL_PASSWORD: str | None = None  # 支持 Personal Access Token
    GIT_SSH_KEY_PATH: str | None = None     # 例如 /root/.ssh/id_rsa

    # ── 安全配置 ──
    SECRET_KEY: str = "YOUR_SUPER_SECRET_KEY_HERE"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # ── 初始超级管理员配置 ──
    FIRST_SUPERUSER_EMAIL: str = "admin@admin.com"
    FIRST_SUPERUSER_PASSWORD: str = "admin"

    # ── 时区配置 ──
    # 所有节点的业务时间戳统一使用此时区，通过 docker-compose.yml 环境变量覆盖
    TIMEZONE: str = "Asia/Shanghai"

    # ── 节点识别配置 ──
    NODE_ROLE: str = "master"  # 可选: master, worker
    NODE_ID: str = _get_persistent_node_id()

    # ── 心跳配置 ──
    HEARTBEAT_INTERVAL: int = 5       # 心跳发送间隔（秒）
    HEARTBEAT_TTL: int = 15           # 心跳 Redis 键过期时间（秒）

    # ── 调度器配置 ──
    SCHEDULER_THREAD_POOL_SIZE: int = 50
    SCHEDULER_MAX_INSTANCES: int = 100
    SCHEDULER_MISFIRE_GRACE_TIME: int = 60

    # ── 任务配置 ──
    TASK_STATUS_EXPIRE_SECONDS: int = 7 * 24 * 3600  # 任务状态在 Redis 中的过期时间

    # ── Redis 队列键名 ──
    PUBLIC_QUEUE_KEY: str = "task:queue:public"
    NODE_QUEUE_PREFIX: str = "task:queue:"
    INGEST_QUEUE_KEY: str = "spider:data:stream"

    # ── Worker 日志缓冲 ──
    LOG_FLUSH_SIZE: int = 20          # 每 N 行 flush 一次
    LOG_FLUSH_INTERVAL: float = 2.0   # 最多 M 秒 flush 一次

    # ── 实时日志热缓冲 (WebSocket 迟到补偿) ──
    # 每条日志在广播 Pub/Sub 的同时写入 Redis List，供迟到的 WebSocket 客户端回放
    LOG_HOTBUF_PREFIX: str = "log:hotbuf:"  # Redis List Key 前缀
    LOG_HOTBUF_MAX: int = 200               # 每个任务最多保留最近 N 条
    LOG_HOTBUF_TTL: int = 7 * 24 * 3600    # 热缓冲 Key 过期时间（秒），与任务状态 TTL 一致


    # ── Data Reducer 批量入库 ──
    REDUCER_BATCH_SIZE: int = 100         # 累积 N 条数据触发一次批量写入
    REDUCER_FLUSH_INTERVAL: float = 1.0   # 最多 M 秒强制 flush 一次

    # ── Docker 容器资源限制 (DooD) ──
    DOCKER_MEM_LIMIT: str = "512m"
    DOCKER_CPU_PERIOD: int = 100_000   # CFS 调度器周期 (µs)
    DOCKER_CPU_QUOTA: int = 100_000    # 等效 1 核 CPU
    DOCKER_NETWORK: str = "spidermanage_net"

    # 爬虫容器内回连后端的地址
    # 容器(爬虫)通过 Docker 内部网络访问 Master API 的地址
    # Docker Compose 模式：通过环境变量覆盖为 http://master:8000（服务名解析）
    # 本地开发模式(宿主机运行后端): 设为 http://host.docker.internal:8000
    SPIDER_API_URL: str = "http://host.docker.internal:8000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # 忽略环境中未在 Settings 中声明的变量
    )


# 实例化全局配置对象，供其他模块依赖注入使用
settings = Settings()
