#!/bin/bash
# FDE 平台看门狗
# 功能：定期 health check，服务异常时自动触发重启
# 运行：由 launchd 托管，开机自启
#
# 用法：./watchdog.sh          # 前台运行（调试）
#       ./watchdog.sh --once   # 只执行一次检查（cron 场景）

set -uo pipefail

# ====== 配置 ======
SERVICE_URL="${FDE_SERVICE_URL:-https://127.0.0.1:8000}"
INTERVAL="${FDE_WATCHDOG_INTERVAL:-30}"          # 检查间隔（秒）
FAILURE_THRESHOLD="${FDE_WATCHDOG_THRESHOLD:-3}"  # 连续失败多少次才重启
LOG_DIR="$(cd "$(dirname "$0")" && pwd)/logs"
LOG_FILE="${LOG_DIR}/watchdog.log"
LAUNCHD_LABEL="com.fde.platform"
PID_FILE="$(cd "$(dirname "$0")" && pwd)/run/platform.pid"

mkdir -p "$LOG_DIR"

# ====== 工具函数 ======
log() {
    local level="$1"; shift
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

# 检查服务是否存活（TCP 端口层）
check_alive() {
    curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$SERVICE_URL/health/live" 2>/dev/null
}

# 检查服务是否就绪（依赖层：Postgres、Redis）
check_ready() {
    curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$SERVICE_URL/health/ready" 2>/dev/null
}

# 通过 launchd 重启服务
restart_via_launchd() {
    log "WARN" "触发 launchd 重启: $LAUNCHD_LABEL"
    launchctl kickstart -k "gui/$(id -u)/${LAUNCHD_LABEL}" 2>&1 | tee -a "$LOG_FILE" || true
    log "WARN" "等待服务重启 (15s)..."
    sleep 15
}

# 暴力 kill 端口上的进程 + launchd 重启（极端兜底）
force_recover() {
    log "CRIT" "执行强制恢复：清理僵尸进程 + 重启"
    # 找到 8000 端口占用的进程并 kill
    local pids
    pids=$(lsof -ti :8000 2>/dev/null || true)
    if [ -n "$pids" ]; then
        log "CRIT" "kill 端口 8000 上的进程: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 2
    fi
    # 移除可能残留的 pid 文件
    rm -f "$PID_FILE"
    # 先 unload 再 load，确保干净重启
    launchctl bootout "gui/$(id -u)" "$(dirname "$0")/com.fde.platform.plist" 2>/dev/null || true
    sleep 1
    launchctl bootstrap "gui/$(id -u)" "$(dirname "$0")/com.fde.platform.plist" 2>&1 | tee -a "$LOG_FILE" || true
    sleep 15
}

# ====== 主循环 ======
fail_count=0
consecutive_restarts=0
MAX_CONSECUTIVE_RESTARTS=5  # 连续重启超过此次数则认为服务有硬故障

run_once="${1:-}"

log "INFO" "=== FDE Watchdog 启动 ==="
log "INFO" "目标: $SERVICE_URL | 间隔: ${INTERVAL}s | 阈值: ${FAILURE_THRESHOLD}"

while true; do
    # 1) liveness probe - HTTP 200 = 活着
    live_code=$(check_alive)
    if [ "$live_code" = "200" ]; then
        # 2) readiness probe - HTTP 200 = 依赖正常
        ready_code=$(check_ready)
        if [ "$ready_code" = "200" ]; then
            if [ $fail_count -gt 0 ] || [ $consecutive_restarts -gt 0 ]; then
                log "INFO" "服务恢复正常 (live=$live_code, ready=$ready_code)"
            fi
            fail_count=0
            consecutive_restarts=0
        else
            log "WARN" "服务活着但未就绪 (ready=$ready_code) — 依赖可能异常"
            fail_count=$((fail_count + 1))
        fi
    else
        fail_count=$((fail_count + 1))
        log "WARN" "服务无响应 (live=$live_code) — 第 ${fail_count}/${FAILURE_THRESHOLD} 次"
    fi

    # 触发重启
    if [ $fail_count -ge $FAILURE_THRESHOLD ]; then
        consecutive_restarts=$((consecutive_restarts + 1))
        if [ $consecutive_restarts -gt $MAX_CONSECUTIVE_RESTARTS ]; then
            log "CRIT" "连续重启 ${consecutive_restarts} 次仍未恢复，疑似硬故障！"
            force_recover
            consecutive_restarts=0
        else
            restart_via_launchd
        fi
        fail_count=0

        # 重启后立即验证
        post_live=$(check_alive)
        post_ready=$(check_ready)
        log "INFO" "重启后检查: live=$post_live ready=$post_ready"
    fi

    [ "$run_once" = "--once" ] && break
    sleep "$INTERVAL"
done
