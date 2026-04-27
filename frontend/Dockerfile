# ============================================
# 阶段 1: 构建前端静态资源
# ============================================
FROM node:18-alpine AS builder

WORKDIR /app

# 先复制依赖定义文件，利用 Docker 缓存层加速构建
COPY package.json package-lock.json ./

# 安装依赖
RUN npm ci --no-audit --no-fund

# 复制源码并构建
COPY . .
RUN npm run build

# ============================================
# 阶段 2: Nginx 托管静态资源
# ============================================
FROM nginx:1.25-alpine

# 设置时区
ENV TZ=Asia/Shanghai
RUN apk add --no-cache tzdata && \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone

# 复制自定义 Nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 从构建阶段复制产物
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
