#!/bin/bash
# 夜间从 GitHub 拉取 main 并做本地 bundle 备份。
# 只在 HEAD 变化时备份；不强拉、不 reset；07:00 收尾后自动卸载调度。
set -uo pipefail

REPO="/Users/longyi/FDE工程"
REMOTE="github"
BRANCH="main"
BACKUP_DIR="$REPO/backups/git"
LOG="$REPO/logs/git-sync.log"
STATE="$BACKUP_DIR/.last-head"
LOCK="$REPO/.git-sync.lock"

mkdir -p "$BACKUP_DIR" "$REPO/logs"

log() { printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

HOUR=$(date +%H)
HOUR_N=$((10#$HOUR))

# 测试用一次性放行标记：存在即跳过时间窗限制，用完即删（不影响正常夜间调度）
if [ -f "$BACKUP_DIR/.force-once" ]; then
    rm -f "$BACKUP_DIR/.force-once"
    HOUR_N=1
    log "测试 | 检测到 .force-once，放行一次（真实执行，仅跳过时间窗判断）"
fi

# 兜底：08:00 之后一律不执行（正常情况下 launchd 只在 01:00-07:00 触发）
if [ "$HOUR_N" -ge 8 ]; then
    log "跳过 | 已过 08:00 触发窗口 | 当前 hour=$HOUR"
    exit 0
fi

if [ ! -d "$REPO/.git" ]; then
    log "失败 | 仓库目录不可用：$REPO"
    exit 1
fi
cd "$REPO" || { log "失败 | 无法进入仓库目录 $REPO"; exit 1; }

if ! mkdir "$LOCK" 2>/dev/null; then
    log "跳过 | 上一轮仍在执行（锁未释放：$LOCK）"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

PREV_HEAD=$(cat "$STATE" 2>/dev/null || echo "unknown")

if ! FETCH_ERR=$(git fetch --prune "$REMOTE" "$BRANCH" 2>&1); then
    log "失败 | git fetch $REMOTE 出错 | 原文：$(echo "$FETCH_ERR" | tr '\n' ' ' | cut -c1-400)"
    exit 1
fi

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse "$REMOTE/$BRANCH" 2>/dev/null || echo "")
if [ -z "$REMOTE_HEAD" ]; then
    log "失败 | 取不到 $REMOTE/$BRANCH 的 commit | 本地 HEAD=$LOCAL_HEAD"
    exit 1
fi

if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
    echo "$LOCAL_HEAD" >"$STATE"
    log "无变化 | HEAD=$LOCAL_HEAD | 上次=$PREV_HEAD | 未备份"
    FINAL_OK=0
else
    DIRTY=$(git status --porcelain --untracked-files=no 2>/dev/null)
    if [ -n "$DIRTY" ]; then
        log "跳过 | 工作区有未提交改动，未拉取 | 本地=$LOCAL_HEAD 远端=$REMOTE_HEAD | 改动：$(echo "$DIRTY" | tr '\n' ';' | cut -c1-300)"
        FINAL_OK=0
    elif ! git merge-base --is-ancestor "$LOCAL_HEAD" "$REMOTE_HEAD" 2>/dev/null; then
        log "跳过 | 本地与远端历史分叉，无法快进 | 本地=$LOCAL_HEAD 远端=$REMOTE_HEAD"
        FINAL_OK=0
    else
        if ! MERGE_ERR=$(git merge --ff-only "$REMOTE_HEAD" 2>&1); then
            log "跳过 | 快进失败 | 本地=$LOCAL_HEAD 远端=$REMOTE_HEAD | 原文：$(echo "$MERGE_ERR" | tr '\n' ' ' | cut -c1-400)"
            FINAL_OK=0
        else
            NEW_HEAD=$(git rev-parse HEAD)
            STAMP=$(date '+%Y-%m-%d-%H')
            BUNDLE="$BACKUP_DIR/$STAMP点.bundle"
            if ! BUNDLE_ERR=$(git bundle create "$BUNDLE" --all 2>&1); then
                log "失败 | bundle 创建失败 | commit=$NEW_HEAD | 原文：$(echo "$BUNDLE_ERR" | tr '\n' ' ' | cut -c1-400)"
                FINAL_OK=1
            else
                printf '%s\n' "$NEW_HEAD" >"$BUNDLE.sha"
                if VERIFY_ERR=$(git bundle verify "$BUNDLE" 2>&1); then
                    echo "$NEW_HEAD" >"$STATE"
                    log "更新 | $LOCAL_HEAD → $NEW_HEAD | 备份=$BUNDLE | 校验=通过"
                    FINAL_OK=0
                else
                    log "失败 | bundle 校验不通过 | commit=$NEW_HEAD | 备份=$BUNDLE | 原文：$(echo "$VERIFY_ERR" | tr '\n' ' ' | cut -c1-300)"
                    FINAL_OK=1
                fi
            fi
        fi
    fi
fi

# 07:00 这一轮是本夜最后一次：先 disable 阻断后续触发，再卸载 job
if [ "$HOUR_N" -eq 7 ]; then
    launchctl disable "gui/$(id -u)/com.fde.git-sync" >/dev/null 2>&1
    log "收尾 | 本轮为 07:00 最后一场，已 disable 并卸载 com.fde.git-sync | 今晚共 7 次"
    launchctl bootout "gui/$(id -u)/com.fde.git-sync" >/dev/null 2>&1
fi

exit "$FINAL_OK"
