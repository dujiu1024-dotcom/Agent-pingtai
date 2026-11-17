#!/bin/bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
echo "=========================================="
echo "  FDE 工作台控制台（归档 v1）"
echo "  地址: http://127.0.0.1:8787/"
echo "  关闭此窗口即停止服务"
echo "=========================================="
echo ""
python3 legacy/v1-archive/console.py --port 8787
echo ""
echo "服务已停止"
read -p "按回车关闭..."
