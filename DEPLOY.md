# SpiderManage 生产部署指南

面向"服务器上已有一个正在服役的 nginx 容器，且**不能改动它的镜像**"的场景。
本文所有步骤均在本地真实 Docker 环境中实测通过（结论见文末「实测记录」）。

---

## 一、端口规划（已写入 `docker-compose.yml`）

| 端口 | 用途 | 是否必须对外 |
|------|------|--------------|
| **4200** | 平台 Web 入口（静态资源 + `/api` + `/ws-*` 反代，由项目自带 nginx 容器提供） | ✅ 必开 |
| **4201** | Master REST API / Swagger `/docs` / `/metrics` | 视需要（供运维直连或上层 nginx 反代） |
| **4203** | MinIO 对象存储管理控制台 | 视需要 |
| **5432** | PostgreSQL，供**服务器内部其他服务**访问 | 内网即可，建议防火墙限制来源 |

不对外发布（仅容器网络 `spidermanage_net` 内互通）：`redis:6379`、`minio:9000(S3 API)`。
需要时在 `docker-compose.yml` 对应服务 `ports` 处放开注释即可。

三个业务端口均可用环境变量覆盖，写入与 `docker-compose.yml` 同目录的 `.env`：

```bash
FRONTEND_PORT=4200
MASTER_PORT=4201
MINIO_CONSOLE_PORT=4203
# DB_HOST_PORT=5432
# REDIS_HOST_PORT=6380
```

---

## 二、完整部署流程（从 GitHub 拉取源码开始）

> 仓库地址：`https://github.com/bjargo/SpiderManager.git`（分支 `main`）
> 私有仓库请使用 `https://<用户名>:<PAT>@github.com/bjargo/SpiderManager.git` 或配置 SSH Deploy Key。

### 步骤 0：服务器前置检查

```bash
uname -a && cat /etc/os-release | head -3          # 需 Linux（DooD 依赖 docker.sock）
docker version --format '{{.Server.Version}}'      # 需 Docker 20.10+
docker compose version                             # 需 Compose v2（docker compose，非 docker-compose）
git --version
ss -lntp | grep -E ':(4200|4201|4203|5432)\b' || echo "端口空闲"   # 应无输出
df -h /opt                                          # 预留 ≥10G
```

### 步骤 1：拉取源码

```bash
sudo mkdir -p /opt/spidermanage && sudo chown "$USER" /opt/spidermanage
cd /opt/spidermanage
git clone https://github.com/bjargo/SpiderManager.git .
git log --oneline -1        # 确认拉到最新提交
```

### 步骤 2：写入生产变量 `.env`（不随 Git 下发，务必手动创建）

```bash
cd /opt/spidermanage
cat > .env <<'EOF'
# ── 邮件通道（163 邮箱，用于每日任务统计日报）──
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_USE_SSL=true
SMTP_START_TLS=false
SMTP_USER=发件邮箱@163.com
SMTP_PASSWORD=邮箱授权码
SMTP_FROM=发件邮箱@163.com
DAILY_REPORT_ENABLED=true
DAILY_REPORT_HOUR=8
DAILY_REPORT_MINUTE=0
DAILY_REPORT_RECIPIENTS=收件邮箱@qq.com
DAILY_REPORT_INCLUDE_EMPTY=true

# ── 端口：服务器为本项目分配的 3 个 ──
FRONTEND_PORT=4200
MASTER_PORT=4201
MINIO_CONSOLE_PORT=4203
# DB_HOST_PORT=5432        # 供服务器内其他服务访问（默认已开）
# REDIS_HOST_PORT=6380
EOF
chmod 600 .env
```

### 步骤 3：覆盖生产安全项（`docker-compose.override.yml`，本机文件、不进 Git）

`docker-compose.yml` 里的 `SECRET_KEY` 与初始管理员口令是**演示默认值**，生产必须替换。
`docker compose` 会自动叠加 `docker-compose.override.yml`，因此**无需改动仓库文件**：

```bash
cd /opt/spidermanage
cat > docker-compose.override.yml <<'EOF'
# 生产环境覆盖项（本机文件，不提交到 Git；git pull 不会冲突）
x-prod-env: &prod-env
  SECRET_KEY: "请替换为 64 位随机串"                 # openssl rand -hex 32
  FIRST_SUPERUSER_EMAIL: "ops@your-domain.com"
  FIRST_SUPERUSER_PASSWORD: "请替换为强密码"

services:
  master:
    environment:
      <<: *prod-env
  worker:
    environment:
      <<: *prod-env        # Worker 与 Master 的 SECRET_KEY 必须一致（镜像缓存指纹依赖它）
    # 如需水平扩容：docker compose up -d --scale worker=3
EOF
chmod 600 docker-compose.override.yml
```

### 步骤 4：构建并启动全部服务

```bash
cd /opt/spidermanage
docker compose up -d --build          # 首次约 3~8 分钟（构建前端与后端镜像）
docker compose ps                     # 期望 6 个服务 Up
```

### 步骤 5：上线自检

```bash
# 端口与接口
curl -sfI http://127.0.0.1:4200/ | head -1                 # HTTP/1.1 200 OK
curl -sf  http://127.0.0.1:4201/docs >/dev/null && echo "API OK"
curl -sfI http://127.0.0.1:4203/ | head -1                 # MinIO 控制台

# 数据库（本项目）与容器网络
docker exec spidermanage_db psql -U postgres -d spidermanage -c \
  "select count(*) from information_schema.tables where table_schema='public';"

# 数据库对服务器内其他服务可达（会打印 version）
docker run --rm postgres:15-alpine psql \
  "postgresql://postgres:postgrespassword@<服务器内网IP>:5432/spidermanage" -c "select version();"

# 调度与日报
docker logs --tail 60 spidermanage_master 2>&1 | grep -E "daily_task_report|report_settings_sync|Registered"

# 邮件通道实测（用初始管理员账号换取令牌后发测试邮件）
TOKEN=$(curl -s -X POST http://127.0.0.1:4201/api/users/login \
  -d "username=<FIRST_SUPERUSER_EMAIL>&password=<FIRST_SUPERUSER_PASSWORD>" \
  | sed -E 's/.*"access_token":"([^"]+)".*/\1/')
curl -s -X POST http://127.0.0.1:4201/api/reports/settings/test-mail \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"recipients":null}'
```

浏览器登录 `http://服务器IP:4200`（或配置好域名后访问域名），
进入 **系统设置** 页确认「SMTP 通道 = 可用」「下次发送时间」正确，然后**立即修改管理员密码**。

### 步骤 6：开放防火墙（按服务器实际使用的防火墙二选一）

```bash
# firewalld（CentOS/RHEL 系）
sudo firewall-cmd --permanent --add-port=4200/tcp --add-port=4201/tcp --add-port=4203/tcp
sudo firewall-cmd --reload

# ufw（Ubuntu/Debian 系）
sudo ufw allow 4200/tcp && sudo ufw allow 4201/tcp && sudo ufw allow 4203/tcp
```

> ⚠️ **数据库端口安全**：`5432` 默认绑定在 `0.0.0.0`（供服务器内其他服务访问）。
> 若只需内网可达，建议把 `docker-compose.yml` 中 db 的端口改为绑定具体内网网卡，
> 例如 `- "10.0.0.5:5432:5432"`；或在 `DOCKER-USER` 链上限制来源网段
> （注意：Docker 的端口发布**会绕过 ufw/firewalld 常规规则**，需用 iptables 限制）。
> 云服务器还需在**安全组**中放行 4200/4201/4203。

---

## 三、与服务器已有 nginx 共存（不改镜像）

### 方案 A：不接现有 nginx，直接暴露 4200

最简单：访问 `http://服务器IP:4200` 即可，现有 nginx 完全不用动。
适合内网、或已有跳板/堡垒机访问的场景。

### 方案 B：用现有 nginx 做统一入口（域名 / HTTPS）

核心思路：**给现有 nginx 容器追加一个站点配置文件**——挂载配置文件属于「容器运行参数」，
**不修改镜像**，也不需要重建镜像。

**第 1 步：让现有 nginx 能访问到本项目（二选一）**

- **B1（推荐）**：把现有 nginx 接入项目网络
  ```bash
  docker network connect spidermanage_net <现有nginx容器名>
  ```
  实测该命令对**运行中**的容器即时生效，**无需重启、无需 reload 现有 nginx**。
  用配置文件里的：`set $spidermanage_backend spidermanage_frontend:80;`

- **B2**：不改网络，走宿主机已发布端口
  用配置文件里的：`set $spidermanage_backend 192.168.1.10:4200;`（换成服务器**真实 IP**）
  ⚠️ 容器内不能用 `127.0.0.1` 指向宿主机；实测 `host.docker.internal` 在默认 bridge 网络下不可用。
  若现有 nginx 不在自定义网络内（即没有 Docker 内置 DNS），把配置里的 `resolver 127.0.0.11;` 一行注释掉。

**第 2 步：放入站点配置**

从仓库取 [deploy/nginx/spidermanage.conf](deploy/nginx/spidermanage.conf)，改两处：
`server_name` 改为你的域名；按 B1/B2 选择 `set $spidermanage_backend`。

```bash
# 方式①：直接复制进容器并 reload（验证用，容器重建后失效）
docker cp spidermanage.conf <现有nginx容器名>:/etc/nginx/conf.d/spidermanage.conf
docker exec <现有nginx容器名> nginx -t && docker exec <现有nginx容器名> nginx -s reload

# 方式②（推荐，长期有效）：启动现有 nginx 时多挂一个文件
#   -v /opt/spidermanage/spidermanage.conf:/etc/nginx/conf.d/spidermanage.conf:ro
```

**第 3 步：域名解析到服务器**，访问 `http://你的域名` 即为平台首页。

> ⚠️ **踩坑提醒（实测）**：在 Windows 上编辑配置后上传，若文件带 UTF-8 BOM，
> nginx 会报 `unknown directive "#"` 而启动失败。请保存为**无 BOM 的 UTF-8**：
> ```bash
> sed -i '1s/^\xEF\xBB\xBF//' /opt/spidermanage/spidermanage.conf
> ```

### 方案 C：不想再多跑一个 nginx 容器

把 `frontend` 的构建产物 `dist/` 交给现有 nginx 托管，并自行反代 `/api`、`/ws-logs`、`/ws-data`。
代价是要在现有 nginx 配置里重复实现 SPA 回退、WebSocket 升级、上传体积等规则，
不如方案 A/B 省事，仅在明确不允许新增 nginx 容器时采用。

---

## 四、升级与回滚

```bash
# 升级（仅重建有改动的服务，数据卷不受影响）
git pull && docker compose up -d --build

# 只更新后端
docker compose up -d --build master

# 回滚：切回上一个版本后
git checkout <上一个tag/commit> && docker compose up -d --build

# 数据备份
docker exec spidermanage_db pg_dump -U postgres spidermanage > backup_$(date +%F).sql
docker exec spidermanage_db pg_dump -U postgres Spider      > backup_spider_$(date +%F).sql
```

---

## 五、上线后自检清单

| 检查项 | 命令 / 位置 | 期望 |
|--------|-------------|------|
| 容器健康 | `docker compose ps` | 6 个服务 Up |
| Web 入口 | `curl -I http://127.0.0.1:4200/` | 200 |
| API | `curl -I http://127.0.0.1:4201/docs` | 200 |
| MinIO 控制台 | 打开 `http://服务器IP:4203` | 登录页 |
| 数据库对内可达 | 用其他服务连 `服务器IP:5432` | 连接成功 |
| 日报配置 | 平台 → 系统设置 | SMTP 就绪、下次发送时间正确 |
| 邮件通道 | 系统设置 → 发送测试邮件 | 收件箱收到（含垃圾箱） |
| 实时日志 | 任务列表 → 打开任务日志 | 日志实时滚动（验证 WebSocket） |
| 定时调度 | `docker logs spidermanage_master \| grep daily_task_report` | 已注册，仅 Leader 一台 |

---

## 六、常见问题（旧版 Docker / 网络受限）

### Q1. `docker compose up -d --build` 报错、无法构建镜像

先确认版本与真实报错：

```bash
docker --version
docker compose version 2>&1 | head -2      # v2（插件）
docker-compose version 2>&1 | head -2      # v1（独立程序，可能只有它）
```

**① Compose 不可用 / 版本过低 → 优先装 v2 插件（推荐）**

```bash
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
```

若旧版 Docker 识别不到 `cli-plugins` 目录，则装独立二进制（同样支持 `docker compose` 用法）：

```bash
sudo curl -SL https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-linux-x86_64 \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose version
```

**② 不想升级 / 升级受限 → 用兼容脚本（任何版本都能跑）**

仓库自带 [deploy/deploy-fallback.sh](deploy/deploy-fallback.sh)：改用 `docker build` 手工构建镜像，
再让 compose 复用镜像，**完全绕开 compose 的构建能力**。

```bash
cd /opt/spidermanage
cp deploy/deploy-fallback.sh .
bash deploy-fallback.sh
```

其等价的手工命令（想逐步执行时用）：

```bash
docker build -t spidermanage-app:latest ./backend      # master + worker 共用
docker build -t spidermanage-web:latest ./frontend

cat > docker-compose.images.yml <<'EOF'
services:
  master:
    image: spidermanage-app:latest
  worker:
    image: spidermanage-app:latest
  frontend:
    image: spidermanage-web:latest
EOF

docker-compose -f docker-compose.yml -f docker-compose.images.yml up -d   # 注意：不加 --build
```

> `docker-compose.images.yml` 是「镜像复用」文件，不修改任何受版本控制的文件；
> 以后装好新版 Compose 想恢复正常构建，删掉它即可：`rm docker-compose.images.yml`。

**③ 报 `Version in "./docker-compose.yml" is unsupported`**

说明 `docker-compose` 低于 1.25，不认识 `version: '3.8'` 与网络的 `name:` 键：

```bash
sed -i "s/^version: .*/version: '3.5'/" docker-compose.yml
docker network create spidermanage_net 2>/dev/null || true   # 保证网络名与 DOCKER_NETWORK 一致
```

> ⚠️ 网络名必须保持 `spidermanage_net`，否则爬虫容器会因找不到网络而无法访问 MinIO/Redis。

**④ 脚本报 `syntax error near unexpected token`**

说明文件被 Windows 编辑成了 CRLF 换行，转换即可：

```bash
sed -i 's/\r$//' deploy-fallback.sh    # 或 dos2unix deploy-fallback.sh
```

> 仓库已通过 `.gitattributes` 固定 `*.sh text eol=lf`，正常 clone 不会出现该问题。

### Q2. 构建时卡住或报拉取失败（pull / TLS / timeout）

服务器无法访问 Docker Hub 时，先配镜像加速或手动预拉基础镜像：

```bash
# 镜像加速（以阿里云个人加速地址为例，换成自己的）
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{ "registry-mirrors": ["https://<你的ID>.mirror.aliyuncs.com"] }
EOF
sudo systemctl restart docker

# 或手动预拉全部基础镜像
docker pull python:3.11-slim
docker pull node:18-alpine
docker pull nginx:1.25-alpine
docker pull postgres:15-alpine
docker pull redis:7-alpine
docker pull minio/minio:RELEASE.2022-10-29T06-21-33Z
```

公司代理环境可在构建时传入：

```bash
docker build --build-arg http_proxy=http://proxy:port --build-arg https_proxy=http://proxy:port \
  -t spidermanage-app:latest ./backend
```

### Q3. 容器起来了但爬虫任务无法执行

Worker 通过挂载的 `/var/run/docker.sock` 创建爬虫容器（DooD），需确认：

```bash
ls -l /var/run/docker.sock                     # 存在且可读写
docker network ls | grep spidermanage_net      # 网络名必须精确一致
docker logs --tail 50 spidermanager-worker-1
```

---

## 七、实测记录（本地真实 Docker 环境）

1. **端口**：`4200→80`、`4201→8000`、`4203→9001`、`5432→5432` 均实测可访问；
   Web 200、`/docs` 200、登录与业务接口正常、MinIO 控制台 200。
2. **数据库对外可达**：用独立容器执行
   `psql postgresql://postgres:***@<服务器IP>:5432/spidermanage` 成功读取到 9 张表。
3. **现有 nginx 共存（B1）**：使用**未做任何修改**的 `nginx:1.25-alpine` 镜像，
   仅挂载站点配置文件；执行 `docker network connect spidermanage_net <容器>` 前后对比：
   - 接入前：平台域名 → `502`（容器名无法解析）
   - 接入后：平台域名 → Web `200`、API 登录成功、WebSocket 升级 `101 Switching Protocols`
   - 同时验证：现有 nginx 的**原站点仍然正常**（默认站点欢迎页 200），两者互不影响
   - 且**未重启现有 nginx 容器**（仅靠请求时解析 + 新网络即时生效）
4. **方案 B2**：容器不在项目网络时，用服务器真实 IP + 已发布端口可正常访问；
   用 `host.docker.internal` 会因默认 bridge 网络无内置 DNS 而超时。
5. **配置字符集**：带 BOM 的配置文件会导致 nginx 启动失败（`unknown directive "#"`）。
