# PersonalCopilot 前端镜像（多阶段：build → nginx 托管静态 + 反代 /api）
FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json ./
# 有 lock 文件就优先用它（可复现构建）
COPY package-lock.json* yarn.lock* pnpm-lock.yaml* ./
# 国内网络加速：npm 换成淘宝镜像源
RUN npm config set registry https://registry.npmmirror.com
RUN if [ -f yarn.lock ]; then yarn install --frozen-lockfile; \
    elif [ -f package-lock.json ]; then npm ci; \
    elif [ -f pnpm-lock.yaml ]; then corepack enable && pnpm install --frozen-lockfile; \
    else npm install; fi

COPY . .
# 同源部署：前端通过 nginx 反代 /api 到后端，构建时注入后端地址
ARG VITE_API_BASE=""
ARG VITE_BACKEND_TARGET="http://backend:8000"
RUN npm run build

# ---- 运行阶段：nginx 托管静态 + 反代 /api（含 SSE 支持）----
FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
