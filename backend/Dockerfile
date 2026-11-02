# PersonalCopilot 后端镜像（FastAPI + LangGraph）
FROM python:3.12-slim

# 国内网络加速：Debian 源换成阿里云镜像，pip 换成清华镜像
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true

# 系统依赖：无编译型依赖，保持精简；tini 用于优雅退出
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存（代码变动不重装依赖）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制后端代码
COPY app ./app

# 数据目录：生产环境用环境变量 DATA_DIR 挂载持久化卷
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data

# 端口
EXPOSE 8000

# tini 作为 PID 1，正确转发信号、回收子进程
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
