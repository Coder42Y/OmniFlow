#!/usr/bin/env bash
# =======================================================
# 聚合AI工作台 (AI Media Studio) 云端生产一键部署/更新脚本
# =======================================================
set -e

echo "=== 正在检查并启动聚合AI工作台容器集群 ==="

if ! command -v docker &> /dev/null; then
    echo "错误: 未检测到 Docker，请先安装 docker 和 docker-compose"
    exit 1
fi

# 检查 .env 配置文件
if [ ! -f .env ]; then
    echo "提示: 未找到 .env 文件，正从 .env.example 生成默认配置..."
    cp .env.example .env
    echo "请根据实际情况配置 .env 中的 API Key 后重新运行此脚本。"
fi

echo "1. 构建并启动容器..."
docker compose down || true
docker compose build --pull
docker compose up -d

echo "2. 检查运行状态..."
sleep 3
docker compose ps

echo "======================================================="
echo "工作台已成功启动！"
echo "客户访问地址: http://<服务器公网IP>:${HOST_PORT:-80}"
echo "健康检查地址: http://<服务器公网IP>:${HOST_PORT:-80}/api/health"
echo "======================================================="
