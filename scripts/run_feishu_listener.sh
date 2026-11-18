#!/bin/bash
# 飞书入站长连接监听：加载部署环境后启动独立进程。
set -euo pipefail
ROOT="/Users/longyi/FDE工程"
cd "$ROOT"
set -a
if [ -f deploy/.env ]; then
  # shellcheck disable=SC1091
  . deploy/.env
fi
set +a
exec "$ROOT/.venv-listener/bin/python" -m agent_platform.feishu.listener
