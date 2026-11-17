#!/bin/bash
cd "$(dirname "$0")"
echo "=========================================="
echo "  FDE 工作台控制台"
echo "  地址: http://127.0.0.1:8787/"
echo "  关闭此窗口即停止服务"
echo "=========================================="
echo ""
python3 console.py --port 8787
echo ""
echo "服务已停止"
read -p "按回车关闭..."
