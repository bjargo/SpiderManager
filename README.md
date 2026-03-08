# SpiderManage

一个现代化的分布式爬虫管理平台，支持爬虫项目管理、任务调度、分布式执行和实时监控。

---

## 📑 目录

- [核心特点](#-核心特点)
- [系统架构](#-系统架构)
- [技术栈](#-技术栈)
- [快速开始](#-快速开始)
- [配置说明](#-配置说明)
- [项目结构](#-项目结构)
- [API 概览](#-api-概览)
- [本地开发](#-本地开发)
- [License](#-license)

---

## ✨ 核心特点

### 🏗️ 分布式 Master-Worker 架构

采用 Master-Worker 分布式架构设计，实现控制平面与执行平面的彻底解耦：

| 角色 | 职责 |
|------|------|
| **Master** | REST API 服务、APScheduler 定时调度、MinIO 客户端初始化、心跳上报、任务队列消费 |
| **Worker** | 任务队列消费、Docker 容器生命周期管理、日志流采集、心跳上报 |

**核心优势**：
- **水平扩展**：通过 `docker compose up -d --scale worker=N` 一键扩容 Worker 节点
- **专属队列**：每个 Worker 节点自动生成唯一 UUID 标识，支持专属任务队列和公共队列的双重消费模式
- **角色灵活**：Master 节点同时具备任务消费能力，小型部署可仅运行 Master

**任务路由机制**：
```
任务下发 → Redis 队列 (LPUSH)
         ├─→ task:queue:public        (公共队列，所有节点竞争消费)
         └─→ task:queue:{node_id}     (专属队列，指定节点消费)
```

---

### 🐳 DooD 容器隔离执行

创新性地采用 **Docker-outside-of-Docker (DooD)** 模式实现爬虫隔离执行：

```
┌─────────────────────────────────────────────────────────────┐
│                     宿主机 (Host)                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Docker Daemon                           │   │
│  │   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │   │
│  │   │ Spider Ctr 1│  │ Spider Ctr 2│  │ Spider Ctr N│ │   │
│  │   └─────────────┘  └─────────────┘  └─────────────┘ │   │
│  └─────────────────────────────────────────────────────┘   │
│                          ▲                                  │
│                          │ docker.sock                      │
│  ┌───────────────────────┴───────────────────────────────┐ │
│  │              Worker Container                          │ │
│  │   ┌──────────────────────────────────────────────┐   │ │
│  │   │  DockerManager (docker-py SDK)               │   │ │
│  │   │  - run_spider_container()                    │   │ │
│  │   │  - stop_container()                          │   │ │
│  │   │  - get_container_logs()                      │   │ │
│  │   └──────────────────────────────────────────────┘   │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**核心特性**：
- **进程级隔离**：每个爬虫任务在独立的 Docker 容器中运行，互不干扰
- **资源限制**：通过 `DOCKER_MEM_LIMIT` / `DOCKER_CPU_QUOTA` 精细控制单容器资源配额
- **自动清理**：容器配置 `auto_remove=True`，任务完成后自动移除，避免资源泄漏
- **网络互通**：爬虫容器自动接入 `spidermanage_net` 网络，可直接访问 MinIO / Redis 等基础设施

**配置示例**：
```yaml
# docker-compose.yml
DOCKER_MEM_LIMIT: 512m      # 单容器内存上限
DOCKER_CPU_QUOTA: 100000    # 1 核 CPU (CFS 调度)
DOCKER_CPU_PERIOD: 100000   # CFS 周期
```

---

### 🚀 智能镜像缓存 (Zero-Download Cache)

独创的镜像构建缓存策略，大幅提升任务启动速度：

```
┌────────────────────────────────────────────────────────────────┐
│                    镜像构建决策流程                              │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  1. 远程指纹探测 (Remote Fingerprint)                          │
│     ├─ Git: git ls-remote → 获取远程分支最新 Commit ID         │
│     └─ MinIO: 对象 ETag / 元数据                               │
│              │                                                 │
│              ▼                                                 │
│  2. 预计算镜像标签                                              │
│     image_tag = spider-{project}-{lang}:{fingerprint[:12]}    │
│              │                                                 │
│              ▼                                                 │
│  3. 本地镜像检查 (Cache Hit?)                                  │
│     ├─ ✅ 存在 → 直接使用，跳过构建 (Zero-Download)            │
│     └─ ❌ 不存在 → 下载源码 → 构建镜像                         │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**性能对比**：

| 场景 | 传统方式 | Zero-Download Cache |
|------|----------|---------------------|
| 重复执行相同任务 | 30+ 秒 (下载+构建) | **毫秒级** (直接复用) |
| 代码未变更的定时任务 | 每次重新构建 | **零构建** |
| 远程探测失败 | N/A | 自动回退本地哈希扫描 |

**实现细节**：
```python
# app/worker/executor.py
remote_fingerprint = await handler.get_remote_fingerprint(source_url, **kwargs)
predict_tag = f"spider-{project_id}-{language}:{remote_fingerprint[:12]}-{script_hash}"

if await image_manager.check_image_exists(predict_tag):
    # Cache Hit! 直接使用，无需下载代码
    task_data["image_tag"] = predict_tag
```

---

### 🔌 插件化多语言支持

采用 **插件架构 (Plugin Architecture)** 实现多语言爬虫支持，新增语言只需添加运行时插件目录：

```
backend/app/core/container/runtimes/
├── python/
│   ├── manifest.json          # 插件元数据
│   ├── Dockerfile.template    # Jinja2 模板
│   └── .dockerignore.template
├── node/
│   ├── manifest.json
│   ├── Dockerfile.template
│   └── .dockerignore.template
└── default/                   # 兜底插件
    └── ...
```

**manifest.json 示例**：
```json
{
  "name": "Python Runner",
  "aliases": ["python", "python3", "python:3.11-slim"],
  "description": "Python 3.11 运行时"
}
```

**Dockerfile.template 示例**：
```dockerfile
# Jinja2 模板，支持变量注入
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true
CMD ["{{ entrypoint }}"]
```

**支持的运行时**：
- **Python** (`python:3.11-slim`) — 自动安装 `requirements.txt`
- **Node.js** (`node:20-slim`) — 自动安装 `package.json`
- **可扩展** — 添加新语言只需创建插件目录

---

### 📊 实时日志流与数据管道

#### 实时日志流

基于 **WebSocket + Redis Pub/Sub** 的实时日志推送架构：

```
┌──────────────┐     WebSocket      ┌──────────────┐
│   Frontend   │◄───────────────────│    Master    │
│  (Browser)   │   /ws-logs/{id}    │   (FastAPI)  │
└──────────────┘                    └──────┬───────┘
                                           │ Redis SUBSCRIBE
                                           ▼
                                    ┌──────────────┐
                                    │    Redis     │
                                    │  Pub/Sub     │
                                    └──────┬───────┘
                                           │ PUBLISH log:channel:{task_id}
                                           ▲
                                    ┌──────┴───────┐
                                    │    Worker    │
                                    │  (Executor)  │
                                    └──────────────┘
```

**特性**：
- **毫秒级延迟**：日志产生即推送，无轮询开销
- **历史回放**：日志同时批量写入 PostgreSQL，支持历史查询
- **断线重连**：WebSocket 断开后前端可自动重连

#### 数据采集管道 (Data Reducer)

爬虫采集数据的高吞吐入库管道：

```
┌──────────────┐    POST /api/tasks/data/ingest    ┌──────────────┐
│ Spider Ctr   │──────────────────────────────────►│    Master    │
│   (SDK)      │    {"t": "table", "d": [...]}     │   (Gateway)  │
└──────────────┘                                    └──────┬───────┘
                                                           │ LPUSH
                                                           ▼
                                                    ┌──────────────┐
                                                    │ Redis Queue  │
                                                    │ spider:data  │
                                                    └──────┬───────┘
                                                           │ BRPOP
                                                           ▼
                                                    ┌──────────────┐
                                                    │Data Reducer  │
                                                    │  (Consumer)  │
                                                    └──────┬───────┘
                                                           │
                                            ┌──────────────┼──────────────┐
                                            ▼              ▼              ▼
                                     ┌────────────┐ ┌────────────┐ ┌────────────┐
                                     │ PostgreSQL │ │ Redis Pub  │ │ WebSocket  │
                                     │  (JSONB)   │ │   /Sub     │ │  推送前端  │
                                     └────────────┘ └────────────┘ └────────────┘
```

**核心优势**：
- **Schema-Free**：使用 JSONB 存储，字段变化无需 DDL 变更
- **自动建表**：根据 `table_name` 自动创建 PostgreSQL 表
- **批量入库**：累积 100 条或 1 秒触发一次批量 INSERT
- **实时分发**：前端可通过 `/ws-data/{task_id}` 订阅采集数据

**自动创建的表结构**：
```sql
CREATE TABLE "{table_name}" (
  "_id"          BIGSERIAL PRIMARY KEY,
  "_task_id"     TEXT NOT NULL,
  "_data"        JSONB NOT NULL,      -- 完整采集数据
  "_created_at"  TIMESTAMP DEFAULT NOW()
);
CREATE INDEX ix_{table}__task_id ON "{table_name}" ("_task_id");
```

---

### ⏰ 强大的任务调度

集成 **APScheduler** 调度器，使用 Redis 作为持久化 JobStore：

```python
# app/core/scheduler.py
jobstores = {
    'default': RedisJobStore(
        jobs_key='apscheduler.jobs',
        run_times_key='apscheduler.run_times',
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT
    )
}
```

**支持的调度模式**：

| 模式 | 说明 | API |
|------|------|-----|
| **即时执行** | 立即触发爬虫任务 | `POST /api/tasks/run` |
| **Cron 定时** | 灵活的 Cron 表达式配置 | `POST /api/tasks/cron` |
| **任务取消** | Redis 信号机制优雅终止 | `POST /api/tasks/{id}/stop` |

**任务取消机制**：
```
用户点击取消 → DB 状态更新为 cancelled → Redis SET task:kill:{id}
                                              │
                                              ▼
Worker 检测到 kill 信号 → docker stop → 容器终止 → 日志推送 "[SYSTEM: Task killed by user]"
```

**孤儿任务清理**：
```python
# main.py - 服务启动时自动清理
async with async_session_maker() as session:
    await session.execute(
        update(SpiderTask)
        .where(SpiderTask.status.in_(["running", "pending"]))
        .values(status="error", error_detail="Orphaned: server restarted")
    )
```

---

### 💓 节点心跳与监控

Worker 节点定期向 Redis 上报心跳，包含系统资源使用情况：

```python
# app/worker/heartbeat.py
async def get_system_stats() -> Dict[str, Any]:
    return {
        "node_id": NODE_ID,
        "role": settings.NODE_ROLE,
        "ip": get_local_ip(),
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": mem.percent,
        "memory_total_mb": mem.total // (1024 * 1024),
        "memory_used_mb": mem.used // (1024 * 1024),
        "disk_usage": disk.percent,
        "timestamp": now().isoformat()
    }
```

**心跳机制**：
- **发送间隔**：`HEARTBEAT_INTERVAL` (默认 5 秒)
- **TTL 过期**：`HEARTBEAT_TTL` (默认 15 秒)，超时自动判定节点离线
- **存储位置**：Redis Key `node:status:{node_id}`

**Prometheus 集成**：
```python
# main.py
from prometheus_fastapi_instrumentator import Instrumentator
instrumentator = Instrumentator()
instrumentator.instrument(app).expose(app, endpoint="/metrics")
```

访问 `http://localhost:8000/metrics` 获取 Prometheus 指标。

---

### 🔐 完整的权限体系

- **JWT 认证**：基于 `python-jose` 的 Token 认证机制
- **角色区分**：超级管理员 (`superuser`) / 普通用户 (`user`)
- **资源隔离**：爬虫项目支持 `owner_id` 字段，实现多租户隔离
- **审计日志**：通过 `AuditContextMiddleware` 自动记录关键操作

```python
# app/core/middleware.py
class AuditContextMiddleware:
    async def dispatch(self, request: Request, call_next):
        # 自动捕获：操作人、IP、时间、请求参数、响应状态
        ...
```

---

### 🐳 一键容器化部署

提供完整的 Docker Compose 编排，支持 **异机部署**：

```yaml
# docker-compose.yml 顶部锚点配置
x-host-db: &host-db db                    # PostgreSQL 地址
x-host-redis: &host-redis redis           # Redis 地址
x-host-minio: &host-minio minio:9000      # MinIO 地址
x-host-master-api: &host-master-api http://master:8000
```

**异机部署**：只需修改顶部锚点，所有引用处自动生效：

```yaml
x-host-db: &host-db 192.168.1.50          # PostgreSQL 部署在 192.168.1.50
x-host-redis: &host-redis 192.168.1.51    # Redis 部署在 192.168.1.51
x-host-minio: &host-minio 192.168.1.52:9000
```

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (React)                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ 项目管理  │  │ 爬虫配置  │  │ 任务监控  │  │ 数据大盘  │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │ REST API / WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Master (FastAPI)                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ REST API │  │Scheduler │  │ Heartbeat│  │  MinIO   │    │
│  │ (路由层)  │  │ (APSch.) │  │ (上报)   │  │ (存储)   │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │  Redis   │  │   Data   │  │  Audit   │                  │
│  │ (队列)   │  │ Reducer  │  │  Logger  │                  │
│  └──────────┘  └──────────┘  └──────────┘                  │
└─────────────────────────────────────────────────────────────┘
        │                 │                 │
        ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  PostgreSQL  │  │    Redis     │  │    MinIO     │
│   (业务数据)  │  │ (队列/缓存)  │  │  (代码存储)  │
│              │  │              │  │              │
│ - users      │  │ - task:queue │  │ - projects/  │
│ - projects   │  │ - log:channel│  │   └─xxx.zip  │
│ - spiders    │  │ - node:status│  │              │
│ - tasks      │  │ - apscheduler│  │              │
│ - task_logs  │  │              │  │              │
└──────────────┘  └──────────────┘  └──────────────┘
                          │
                          ▼ (任务队列 BLPOP)
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   Worker 1   │  │   Worker 2   │  │   Worker N   │
│  ┌────────┐  │  │  ┌────────┐  │  │  ┌────────┐  │
│  │Executor│  │  │  │Executor│  │  │  │Executor│  │
│  └────────┘  │  │  └────────┘  │  │  └────────┘  │
│  ┌────────┐  │  │  ┌────────┐  │  │  ┌────────┐  │
│  │ Docker │  │  │  │ Docker │  │  │  │ Docker │  │
│  │Manager │  │  │  │Manager │  │  │  │Manager │  │
│  └────────┘  │  │  └────────┘  │  │  └────────┘  │
└──────────────┘  └──────────────┘  └──────────────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼ docker.sock
                   ┌──────────────┐
                   │ Docker Daemon│
                   │   (宿主机)    │
                   │ ┌──────────┐ │
                   │ │Spider Ctr│ │ ← 每个任务独立容器
                   │ └──────────┘ │
                   └──────────────┘
```

---

## 🛠️ 技术栈

### 后端

| 组件 | 技术 | 用途 |
|------|------|------|
| **Web 框架** | FastAPI | REST API、WebSocket |
| **ORM** | SQLModel + SQLAlchemy | 数据库模型定义与操作 |
| **数据库** | PostgreSQL | 业务数据持久化 |
| **缓存/队列** | Redis | 任务队列、Pub/Sub、心跳状态 |
| **对象存储** | MinIO | 爬虫代码包存储 |
| **任务调度** | APScheduler | Cron 定时任务 |
| **容器管理** | docker-py | DooD 模式容器生命周期管理 |
| **认证** | python-jose (JWT) | 用户认证与授权 |
| **监控** | prometheus-fastapi-instrumentator | Prometheus 指标暴露 |

### 前端

| 组件 | 技术 | 用途 |
|------|------|------|
| **框架** | React 18 + TypeScript | UI 组件化开发 |
| **构建工具** | Vite | 开发服务器与打包 |
| **状态管理** | Zustand | 轻量级全局状态 |
| **图表** | ECharts | 数据可视化 |
| **代码编辑器** | CodeMirror 6 | 代码编辑与语法高亮 |
| **HTTP 客户端** | Axios | API 请求 |

### 基础设施

| 组件 | 技术 | 用途 |
|------|------|------|
| **容器化** | Docker + Docker Compose | 服务编排与部署 |
| **监控** | Prometheus (可选) | 指标采集与告警 |

---

## 📋 前置要求

- Docker 20.10+
- Docker Compose 2.0+
- (可选) Node.js 18+ 和 Python 3.11+ 用于本地开发

---

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/SpiderManage.git
cd SpiderManage
```

### 2. 启动服务

```bash
docker compose up -d
```

首次启动会自动：
- 创建 PostgreSQL 数据库
- 初始化 Redis
- 启动 MinIO 对象存储
- 创建默认超级管理员账号

### 3. 访问服务

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:5173 |
| 后端 API | http://localhost:8000 |
| API 文档 (Swagger) | http://localhost:8000/docs |
| MinIO 控制台 | http://localhost:9001 |
| Prometheus 指标 | http://localhost:8000/metrics |

### 4. 默认账号

- **邮箱**: `admin@admin.com`
- **密码**: `admin`

> ⚠️ 生产环境请务必修改默认密码！

### 5. Worker 水平扩展

```bash
# 启动 3 个 Worker 实例
docker compose up -d --scale worker=3
```

---

## ⚙️ 配置说明

### 环境变量

主要配置通过 `docker-compose.yml` 中的环境变量管理：

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `SECRET_KEY` | JWT 签名密钥 | *生产环境必须修改* |
| `FIRST_SUPERUSER_EMAIL` | 初始管理员邮箱 | `admin@admin.com` |
| `FIRST_SUPERUSER_PASSWORD` | 初始管理员密码 | `admin` |
| `HEARTBEAT_INTERVAL` | 心跳间隔(秒) | `5` |
| `HEARTBEAT_TTL` | 心跳超时(秒) | `15` |
| `DOCKER_MEM_LIMIT` | 爬虫容器内存限制 | `512m` |
| `DOCKER_CPU_QUOTA` | 爬虫容器 CPU 配额 | `100000` (1核) |
| `LOG_FLUSH_SIZE` | 日志缓冲行数 | `20` |
| `REDUCER_BATCH_SIZE` | 数据批量入库条数 | `100` |

### 异机部署

如需将数据库、Redis、MinIO 部署在不同机器，只需修改 `docker-compose.yml` 顶部的锚点配置：

```yaml
x-host-db: &host-db 192.168.1.50        # PostgreSQL 地址
x-host-redis: &host-redis 192.168.1.51  # Redis 地址
x-host-minio: &host-minio 192.168.1.52:9000  # MinIO 地址
x-host-master-api: &host-master-api http://192.168.1.100:8000
```

### Git 私有仓库配置

支持配置全局 Git 凭证，用于拉取私有仓库：

```yaml
environment:
  GIT_GLOBAL_USERNAME: your-username
  GIT_GLOBAL_PASSWORD: your-personal-access-token
  # 或使用 SSH 密钥
  GIT_SSH_KEY_PATH: /root/.ssh/id_rsa
```

---

## 📁 项目结构

```
SpiderManage/
├── backend/                      # 后端服务
│   ├── app/
│   │   ├── api/                  # API 路由层
│   │   │   ├── admin/            # 管理员接口
│   │   │   ├── dashboard/        # 数据大盘接口
│   │   │   ├── logs/             # 日志查询接口
│   │   │   ├── nodes/            # 节点管理接口
│   │   │   ├── projects/         # 项目管理接口
│   │   │   ├── spiders/          # 爬虫管理接口
│   │   │   ├── tasks/            # 任务管理接口
│   │   │   └── users/            # 用户认证接口
│   │   ├── core/                 # 核心模块
│   │   │   ├── audit/            # 审计日志服务
│   │   │   ├── container/        # 容器管理
│   │   │   │   ├── runtimes/     # 语言运行时插件
│   │   │   │   ├── image_manager.py   # 镜像构建管理
│   │   │   │   └── runners.py    # 插件加载器
│   │   │   ├── schemas/          # 公共 Schema
│   │   │   ├── source/           # 源码处理器
│   │   │   │   ├── base.py       # 抽象基类
│   │   │   │   ├── git_handler.py     # Git 仓库处理
│   │   │   │   ├── minio_handler.py   # MinIO 存储处理
│   │   │   │   └── factory.py    # 处理器工厂
│   │   │   ├── storage/          # 存储客户端
│   │   │   ├── redis.py          # Redis 连接池管理
│   │   │   ├── scheduler.py      # APScheduler 配置
│   │   │   ├── middleware.py     # 审计中间件
│   │   │   └── dependencies.py   # FastAPI 依赖注入
│   │   ├── db/                   # 数据库层
│   │   │   ├── database.py       # 连接池与 Session 管理
│   │   │   └── init_data.py      # 初始数据填充
│   │   └── worker/               # Worker 执行层
│   │       ├── executor.py       # 任务执行器 (核心)
│   │       ├── docker_manager.py # Docker 容器管理
│   │       ├── data_reducer.py   # 数据入库消费者
│   │       ├── heartbeat.py      # 节点心跳上报
│   │       ├── project_loader.py # 源码加载器
│   │       └── cron_jobs.py      # 定时任务触发器
│   ├── main.py                   # FastAPI 入口 (Master)
│   ├── worker_main.py            # Worker 独立入口
│   ├── config.py                 # pydantic-settings 配置
│   ├── requirements.txt          # Python 依赖
│   └── Dockerfile
├── frontend/                     # 前端服务
│   ├── src/
│   │   ├── components/           # React 组件
│   │   ├── pages/                # 页面
│   │   ├── stores/               # Zustand 状态管理
│   │   └── services/             # API 服务封装
│   ├── package.json
│   └── vite.config.ts
└── docker-compose.yml            # Docker Compose 编排
```

---

## 🔌 API 概览

### REST API

| 模块 | 端点前缀 | 说明 |
|------|----------|------|
| 用户认证 | `/api/users` | 登录、注册、用户管理 |
| 项目管理 | `/api/projects` | 爬虫项目 CRUD |
| 爬虫管理 | `/api/spiders` | 爬虫配置管理 |
| 任务管理 | `/api/tasks` | 任务创建、执行、状态查询 |
| 节点管理 | `/api/nodes` | Worker 节点状态监控 |
| 日志查询 | `/api/logs` | 任务日志查询 |
| 数据大盘 | `/api/dashboard` | 统计数据聚合 |
| 管理员 | `/api/admin` | 系统管理功能 |

### WebSocket 端点

| 端点 | 说明 |
|------|------|
| `/ws-logs/{task_id}` | 实时任务日志流 |
| `/ws-data/{task_id}` | 实时爬虫数据流 |

### 核心接口示例

**下发任务**：
```bash
POST /api/tasks/run
{
  "task_id": "task-uuid",
  "spider_id": 1,
  "script_path": "python main.py",
  "target_node_ids": ["node-uuid-1"],  // 可选，指定节点
  "timeout_seconds": 3600
}
```

**数据接入**：
```bash
POST /api/tasks/data/ingest?task_id=xxx
{
  "table_name": "products",
  "data": [
    {"name": "商品A", "price": 99.9},
    {"name": "商品B", "price": 199.9}
  ]
}
```

**创建定时任务**：
```bash
POST /api/tasks/cron
{
  "spider_id": 1,
  "cron_expr": "0 3 * * *",  // 每天凌晨 3 点
  "description": "每日数据采集",
  "enabled": true
}
```

---

## 🧪 本地开发

### 后端开发

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 前端开发

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

### 运行测试

```bash
cd backend
pytest
```

---

## 📄 License

本项目基于 [Apache License 2.0](LICENSE) 开源。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request