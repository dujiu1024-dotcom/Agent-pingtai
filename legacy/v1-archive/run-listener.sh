#!/bin/bash
# Archived v1 listener watchdog. Keep the repository root as the working directory.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
$PY -c "import lark_oapi" 2>/dev/null || {
  echo "缺少 lark-oapi：请先运行 $PY -m pip install -r requirements-listener.txt" >&2
  exit 1
}
while true; do
  mkdir -p agency/state
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] listener 启动..." >> agency/state/listener.watchdog.log
  $PY legacy/v1-archive/listener.py >> agency/state/listener.out.log 2>> agency/state/listener.err.log
  RC=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] listener 退出 rc=$RC, 5 秒后重启" >> agency/state/listener.watchdog.log
  sleep 5
done
