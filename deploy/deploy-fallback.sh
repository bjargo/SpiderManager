#!/usr/bin/env bash
# ==============================================================================
# SpiderManage 兼容性部署脚本（适用于 Docker / Compose 版本较旧的服务器）
#
# 用法：把本文件放到项目根目录（与 docker-compose.yml 同级）后执行
#         bash deploy-fallback.sh
#
# 原理：用 docker build 手工构建镜像，再通过 docker-compose.images.yml 让 compose
#       直接复用这些镜像，全程不使用 `compose build`，因此不依赖高版本 Compose 的
#       构建能力（旧版 docker-compose v1 同样可用）。
#
# 该脚本不会修改任何受版本控制的文件；生成物 docker-compose.images.yml 可随时删除。
# ==============================================================================
set -euo pipefail

cd "$(dirname "$0")"

APP_IMAGE="spidermanage-app:latest"   # 后端镜像（master 与 worker 共用同一镜像）
WEB_IMAGE="spidermanage-web:latest"   # 前端镜像

if ! command -v docker >/dev/null 2>&1; then
    echo "!! 未检测到 docker，请先安装 Docker"
    exit 1
fi

if [ ! -f .env ]; then
    echo "!! 未找到 .env 文件（SMTP 与端口配置不会随 Git 下发）"
    echo "   请先按 DEPLOY.md「步骤 2」创建 .env 后再执行本脚本"
    exit 1
fi

echo "Docker 版本: $(docker version --format '{{.Server.Version}}' 2>/dev/null || docker -v)"

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "!! 未找到 compose（docker compose 与 docker-compose 均不可用）"
    echo "   安装方式见 DEPLOY.md「常见问题 · Q1」"
    exit 1
fi
echo "使用命令: $COMPOSE ($($COMPOSE version --short 2>/dev/null || echo '版本未知'))"

# docker-compose v1 低于 1.25 时不支持本仓库使用的 `version: '3.8'` 与网络 name 键
if [ "$COMPOSE" = "docker-compose" ]; then
    ver="$($COMPOSE version --short 2>/dev/null | tr -d 'v' || true)"
    major="${ver%%.*}"
    minor="$(echo "$ver" | cut -d. -f2)"
    if [ "$major" = "1" ] && [ -n "$minor" ] && [ "$minor" -lt 25 ] 2>/dev/null; then
        echo "!! docker-compose v$ver 不支持 version: '3.8'，请先执行："
        echo "     sed -i \"s/^version: .*/version: '3.5'/\" docker-compose.yml"
        echo "     docker network create spidermanage_net 2>/dev/null || true"
        echo "   （网络的 name 键需 >=1.25；上述命令保证网络名仍为 spidermanage_net，"
        echo "     否则爬虫容器会因找不到网络而无法访问 minio/redis）"
        exit 1
    fi
fi

echo "==> 1/4 构建后端镜像（master + worker 共用）"
docker build -t "$APP_IMAGE" ./backend

echo "==> 2/4 构建前端镜像"
docker build -t "$WEB_IMAGE" ./frontend

echo "==> 3/4 生成镜像复用文件 docker-compose.images.yml"
cat > docker-compose.images.yml <<EOF
# 由 deploy-fallback.sh 生成：让 compose 复用 docker build 出的镜像，不再自行构建
services:
  master:
    image: $APP_IMAGE
  worker:
    image: $APP_IMAGE
  frontend:
    image: $WEB_IMAGE
EOF

echo "==> 4/4 启动全部服务（不加 --build）"
FILES="-f docker-compose.yml"
if [ -f docker-compose.override.yml ]; then
    FILES="$FILES -f docker-compose.override.yml"   # 保留生产覆盖（SECRET_KEY 等）
    echo "    已叠加 docker-compose.override.yml"
else
    echo "    提示：未发现 docker-compose.override.yml，生产环境建议创建以覆盖 SECRET_KEY 与管理员口令"
fi
FILES="$FILES -f docker-compose.images.yml"

# shellcheck disable=SC2086
$COMPOSE $FILES up -d
# shellcheck disable=SC2086
$COMPOSE $FILES ps

cat <<EOF

完成。后续命令请保持相同的文件顺序：
  日志：$COMPOSE $FILES logs -f --tail=200 master
  重启：$COMPOSE $FILES restart master
  停止：$COMPOSE $FILES down

升级流程：git pull && bash deploy-fallback.sh
EOF