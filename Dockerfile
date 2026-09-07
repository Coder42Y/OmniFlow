# 聚合AI工作台 (AI Media Workbench) 生产部署 Dockerfile
FROM python:3.11-slim

LABEL maintainer="AI Media Studio Team"
LABEL description="Production Docker image for AI Media Workbench with FFmpeg and Multi-provider API Gateway"

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai

WORKDIR /app

# 安装 ffmpeg 媒体处理套件及 curl、tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码与前端静态文件
COPY . /app/

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/api/health || exit 1

CMD ["python3", "server.py", "--port", "8080"]
