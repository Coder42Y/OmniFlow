#!/usr/bin/env bash
# 聚合AI工作台一键启动脚本
set -e

PORT=${1:-8080}
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "正在启动聚合AI工作台 (AI Media Studio)..."
echo "端口: $PORT"
echo "目录: $DIR"

python3 "$DIR/server.py" --port "$PORT"
