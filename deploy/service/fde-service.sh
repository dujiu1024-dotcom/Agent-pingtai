#!/bin/bash
# FDE 平台服务管理器
# 命令: install | uninstall | start | stop | restart | status | logs | test | tail-logs

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"

# === launchd 标签与 plist 映射 ===
PLATFORM_PLIST="com.fde.platform.plist"
WATCHDOG_PLIST="com.fde.watchdog.plist"
LISTENER_PLIST="com.fde.feishu-listener.plist"
PLATFORM_LABEL="com.fde.platform"
WATCHDOG_LABEL="com.fde.watchdog"
LISTENER_LABEL="com.fde.feishu-listener"

# 所有 launchd 组件（顺序：先 listener，再 platform，最后 watchdog 监控）
ALL_LABELS=("$LISTENER_LABEL" "$PLATFORM_LABEL" "$WATCHDOG_LABEL")
ALL_PLISTS=("$LISTENER_PLIST" "$PLATFORM_PLIST" "$WATCHDOG_PLIST")

LOG_DIR="$SCRIPT_DIR/logs"
RUN_DIR="$SCRIPT_DIR/run"
SERVICE_URL="https://127.0.0.1:8000"
UID_GUI="gui/$(id -u)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

banner() { echo -e "${BLUE}======== FDE Platform Service ========${NC}"; }
ok() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; }
info() { echo -e "${BLUE}[info]${NC} $*"; }

ensure_dirs() {
    mkdir -p "$LOG_DIR" "$RUN_DIR" "$AGENTS_DIR"
    touch "$LOG_DIR/platform.stdout.log" "$LOG_DIR/platform.stderr.log"
    touch "$LOG_DIR/watchdog.log" "$LOG_DIR/watchdog.stdout.log" "$LOG_DIR/watchdog.stderr.log"
    touch "$LOG_DIR/listener.stdout.log" "$LOG_DIR/listener.stderr.log"
}

copy_plists() {
    local failed=0
    for plist in "${ALL_PLISTS[@]}"; do
        cp "$SCRIPT_DIR/$plist" "$AGENTS_DIR/" || { fail "复制 $plist 失败"; failed=1; continue; }
        plutil -lint "$AGENTS_DIR/$plist" >/dev/null || { fail "$plist 格式错误"; failed=1; continue; }
    done
    [ $failed -eq 0 ] && ok "所有 plist 已复制并通过校验" || return 1
}

label_state() {
    local label="$1"
    if launchctl print "$UID_GUI/$label" 2>/dev/null | grep -q "state = running"; then
        echo "running"
    elif launchctl print "$UID_GUI/$label" 2>/dev/null | grep -q "state = exited"; then
        echo "exited"
    elif launchctl print "$UID_GUI/$label" 2>/dev/null | grep -q "state = waiting"; then
        echo "waiting"
    else
        echo "not_loaded"
    fi
}

print_state_dot() {
    local state="$1"
    case "$state" in
        running)  echo -e "${GREEN}● running${NC}" ;;
        waiting)  echo -e "${YELLOW}● waiting${NC}" ;;
        exited)   echo -e "${RED}● exited${NC}" ;;
        *)        echo -e "${RED}● not_loaded${NC}" ;;
    esac
}

# ==================== 命令实现 ====================

cmd_install() {
    banner
    info "安装 FDE 平台持久化服务..."

    # 先清理可能残留的老实例
    for plist in "${ALL_PLISTS[@]}"; do
        launchctl bootout "$UID_GUI" "$AGENTS_DIR/$plist" 2>/dev/null || true
    done

    ensure_dirs
    copy_plists || return 1

    # 停止手动启动的旧 platform 进程
    local old_pid
    old_pid=$(lsof -ti :8000 2>/dev/null || true)
    if [ -n "$old_pid" ]; then
        warn "发现手动启动的 platform 进程 PID=$old_pid, 正在停止..."
        echo "$old_pid" | xargs kill 2>/dev/null || true
        sleep 2
    fi

    # 依次加载：listener → platform → watchdog
    for i in "${!ALL_PLISTS[@]}"; do
        local plist="${ALL_PLISTS[$i]}"
        local label="${ALL_LABELS[$i]}"
        launchctl bootstrap "$UID_GUI" "$AGENTS_DIR/$plist" \
            && ok "$label 已加载" || { fail "$label 加载失败"; return 1; }
        sleep 1
    done

    # 触发启动
    for label in "${ALL_LABELS[@]}"; do
        launchctl kickstart -k "$UID_GUI/$label" 2>/dev/null || true
    done

    echo ""
    info "等待 platform 服务就绪..."
    for i in $(seq 1 10); do
        local code
        code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 3 "$SERVICE_URL/health/live" 2>/dev/null || echo "000")
        if [ "$code" = "200" ]; then
            ok "platform 上线成功！"
            break
        fi
        sleep 3
    done

    echo ""
    ok "安装完成 ✓"
    info "状态查询: $0 status"
    info "飞书监听日志: $0 tail-logs listener"
    info "platform 日志: $0 tail-logs platform"
}

cmd_uninstall() {
    banner
    info "卸载 FDE 平台持久化服务..."

    for i in "${!ALL_PLISTS[@]}"; do
        local plist="${ALL_PLISTS[$i]}"
        local label="${ALL_LABELS[$i]}"
        launchctl bootout "$UID_GUI" "$AGENTS_DIR/$plist" 2>/dev/null \
            && ok "$label 已卸载" || warn "$label 未运行"
        rm -f "$AGENTS_DIR/$plist"
    done

    ok "所有 plist 文件已移除"
    info "日志保留在 $LOG_DIR/ (如需清理请手动删除)"
}

cmd_start() {
    info "启动所有服务..."
    for label in "${ALL_LABELS[@]}"; do
        local plist_name
        for i in "${!ALL_LABELS[@]}"; do
            [ "${ALL_LABELS[$i]}" = "$label" ] && plist_name="${ALL_PLISTS[$i]}"
        done
        launchctl kickstart -k "$UID_GUI/$label" 2>/dev/null \
            || launchctl bootstrap "$UID_GUI" "$AGENTS_DIR/$plist_name"
    done
}

cmd_stop() {
    info "停止所有服务..."
    for label in "${ALL_LABELS[@]}"; do
        launchctl kill SIGTERM "$UID_GUI/$label" 2>/dev/null || true
    done
    sleep 2
    # 确保没残留 platform
    local pids
    pids=$(lsof -ti :8000 2>/dev/null || true)
    if [ -n "$pids" ]; then
        warn "残留 platform 进程: $pids, 强制终止"
        echo "$pids" | xargs kill -9 2>/dev/null || true
    fi
    ok "所有服务已停止"
}

cmd_restart() {
    banner
    info "重启所有服务..."
    cmd_stop
    sleep 2
    cmd_start
    sleep 10
    cmd_status
}

cmd_status() {
    banner
    echo ""

    printf "  %-22s %-18s\n" "组件" "状态"
    printf "  %-22s %-18s\n" "----------------------" "------------------"

    local state
    state=$(label_state "$LISTENER_LABEL")
    printf "  %-22s " "Feishu Listener"
    print_state_dot "$state"

    state=$(label_state "$PLATFORM_LABEL")
    printf "  %-22s " "FDE Platform"
    print_state_dot "$state"

    state=$(label_state "$WATCHDOG_LABEL")
    printf "  %-22s " "FDE Watchdog"
    print_state_dot "$state"

    echo ""

    # HTTP health
    local live_code ready_code
    live_code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$SERVICE_URL/health/live" 2>/dev/null || echo "000")
    ready_code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$SERVICE_URL/health/ready" 2>/dev/null || echo "000")

    printf "  %-25s " "Platform /health/live"
    [ "$live_code" = "200" ] && echo -e "${GREEN}● OK (200)${NC}" || echo -e "${RED}● FAIL ($live_code)${NC}"
    printf "  %-25s " "Platform /health/ready"
    [ "$ready_code" = "200" ] && echo -e "${GREEN}● OK (200)${NC}" || echo -e "${RED}● FAIL ($ready_code)${NC}"

    # listener WebSocket 检测 — 从最近日志判断
    local listener_pid ws_ok="unknown"
    listener_pid=$(launchctl print "$UID_GUI/$LISTENER_LABEL" 2>/dev/null | grep -E '^\s+pid =' | awk '{print $3}')
    if [ -n "$listener_pid" ]; then
        local recent_line
        recent_line=$(tail -1 "$LOG_DIR/listener.stdout.log" 2>/dev/null || echo "")
        if echo "$recent_line" | grep -qE 'connected to wss'; then
            ws_ok="connected"
        else
            ws_ok="starting"
        fi
        printf "  %-25s " "Feishu WebSocket"
        [ "$ws_ok" = "connected" ] && echo -e "${GREEN}● connected${NC}" || echo -e "${YELLOW}● $ws_ok${NC}"
    fi

    # 运行时长
    local pid etime
    pid=$(lsof -ti :8000 2>/dev/null | head -1 || echo "")
    if [ -n "$pid" ]; then
        etime=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
        echo ""
        info "Platform PID=$pid  运行时长=${etime:-?}"
    fi
    if [ -n "$listener_pid" ]; then
        etime=$(ps -o etime= -p "$listener_pid" 2>/dev/null | tr -d ' ')
        info "Listener  PID=$listener_pid  运行时长=${etime:-?}"
    fi

    # 端口占用
    echo ""
    info "端口监听:"
    lsof -i :8000 -P -n 2>/dev/null | tail -n +2 | while IFS= read -r line; do echo "  $line"; done
}

cmd_logs() {
    banner
    echo "  可用日志:"
    echo "    platform:  stdout=$LOG_DIR/platform.stdout.log"
    echo "               stderr=$LOG_DIR/platform.stderr.log"
    echo "    watchdog:  $LOG_DIR/watchdog.log"
    echo "    listener:  stdout=$LOG_DIR/listener.stdout.log"
    echo "               stderr=$LOG_DIR/listener.stderr.log"
    echo ""
    echo "  使用: $0 tail-logs [platform|watchdog|listener]"
    echo ""
    ls -lh "$LOG_DIR/" 2>/dev/null | tail -n +2
}

cmd_tail_logs() {
    local target="${1:-platform}"
    case "$target" in
        platform)  tail -f "$LOG_DIR/platform.stdout.log" "$LOG_DIR/platform.stderr.log" 2>/dev/null ;;
        watchdog)  tail -f "$LOG_DIR/watchdog.log" 2>/dev/null ;;
        listener)  tail -f "$LOG_DIR/listener.stdout.log" "$LOG_DIR/listener.stderr.log" 2>/dev/null ;;
        *) fail "未知日志类型: $target (可用: platform|watchdog|listener)" ;;
    esac
}

cmd_test() {
    banner
    info "执行完整健康检查..."
    echo ""

    local failed=0

    # 1) launchd 加载检查
    for label in "${ALL_LABELS[@]}"; do
        if launchctl print "$UID_GUI/$label" 2>/dev/null | grep -q "state = running"; then
            ok "$label 运行中"
        else
            fail "$label 未运行"
            failed=$((failed + 1))
        fi
    done

    # 2) liveness
    local code
    code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$SERVICE_URL/health/live" 2>/dev/null || echo "000")
    [ "$code" = "200" ] && ok "Platform /live → 200" || { fail "Platform /live → $code"; failed=$((failed + 1)); }

    # 3) readiness
    code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$SERVICE_URL/health/ready" 2>/dev/null || echo "000")
    if [ "$code" = "200" ]; then
        local detail
        detail=$(curl -sk --max-time 5 "$SERVICE_URL/health/ready" 2>/dev/null || echo "")
        ok "Platform /ready → 200  $detail"
    else
        fail "Platform /ready → $code"; failed=$((failed + 1))
    fi

    # 4) 端口
    lsof -ti :8000 >/dev/null 2>&1 && ok "端口 8000 已监听" || { fail "端口 8000 未监听"; failed=$((failed + 1)); }

    # 5) listener WebSocket — 从日志判断
    local listener_pid
    listener_pid=$(launchctl print "$UID_GUI/$LISTENER_LABEL" 2>/dev/null | grep -E '^\s+pid =' | awk '{print $3}')
    if [ -n "$listener_pid" ]; then
        local last_line
        last_line=$(tail -1 "$LOG_DIR/listener.stdout.log" 2>/dev/null || echo "")
        if echo "$last_line" | grep -qE 'connected to wss'; then
            ok "飞书 WebSocket 已连接"
        else
            warn "Listener 进程存在但日志里未见 connected"
        fi
    fi

    echo ""
    if [ $failed -eq 0 ]; then
        ok "所有检查通过 ✓"
    else
        fail "${failed} 项检查失败"
        return 1
    fi
}

# ==================== 入口 ====================

usage() {
    cat <<EOF
FDE 平台服务管理器（Platform + Watchdog + Feishu Listener）

用法: $0 <命令>

命令:
  install     安装并启动持久化服务（开机自启）
  uninstall   卸载所有服务（保留日志）
  start       启动所有服务
  stop        停止所有服务
  restart     重启所有服务
  status      查看三组件运行状态
  logs        查看日志文件列表
  tail-logs   实时跟踪日志 ($0 tail-logs [platform|watchdog|listener])
  test        执行完整健康检查
  help        显示帮助

首次使用:  $0 install
日常管理:  $0 status / $0 restart / $0 tail-logs listener
EOF
}

ensure_dirs

case "${1:-help}" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    start)     cmd_start ;;
    stop)      cmd_stop ;;
    restart)   cmd_restart ;;
    status)    cmd_status ;;
    logs)      cmd_logs ;;
    tail-logs) cmd_tail_logs "${2:-platform}" ;;
    test)      cmd_test ;;
    help|-h|--help) usage ;;
    *) usage; exit 1 ;;
esac
