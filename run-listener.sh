#!/bin/bash
# run-listener.sh — listener 守护包装：崩了 5 秒后自动拉
# 单套运行环境：不再单独维护 .venv-listener；listener 依赖装进当前 python3。
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
$PY -c "import lark_oapi" 2>/dev/null || {
  echo "缺少 lark-oapi：请先运行 $PY -m pip install -r requirements-listener.txt" >&2
  exit 1
}
while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] listener 启动..." >> agency/state/listener.watchdog.log
  $PY listener.py >> agency/state/listener.out.log 2>> agency/state/listener.err.log
  RC=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] listener 退出 rc=$RC, 5 秒后重启" >> agency/state/listener.watchdog.log
  sleep 5
done
