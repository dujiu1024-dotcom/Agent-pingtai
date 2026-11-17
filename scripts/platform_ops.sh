#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${COMPOSE_FILE:-$ROOT/deploy/production.compose.yaml}"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/.env.production}"
RELEASE_DIR="${RELEASE_DIR:-$ROOT/deploy/releases}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
ACTION="${1:-help}"
CONFIRM="${2:-}"
LOCK_FILE="${OPS_LOCK_FILE:-/tmp/agent-platform-ops.lock}"

usage() {
  cat <<'EOF'
Usage: scripts/platform_ops.sh COMMAND [CONFIRMATION]

Read-only commands:
  preflight       Validate production Compose config and required local tools
  status          Show Compose service state
  health          Check API readiness and dependency container health
  verify-backups  Verify backup checksums/manifests (set BACKUP_DIR)
  upgrade-plan    Validate RELEASE_MANIFEST and print upgrade/rollback plan
  recovery-plan   Print ordered disaster recovery plan

Mutating commands (require exact confirmation token):
  deploy          deploy CONFIRM-PRODUCTION-DEPLOY
  backup          backup CONFIRM-PLATFORM-BACKUP
  restore         restore CONFIRM-PLATFORM-RESTORE
  rollback        rollback CONFIRM-PRODUCTION-ROLLBACK

Environment:
  ENV_FILE defaults to deploy/.env.production
  BACKUP_DIR defaults to backups; AGE_RECIPIENT is required for backup
  RELEASE_MANIFEST is required for upgrade-plan
EOF
}

require_command() { command -v "$1" >/dev/null 2>&1 || { echo "missing command: $1" >&2; exit 2; }; }
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE" "$@"; }
require_env() {
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo "missing or unsafe env file: $ENV_FILE" >&2; exit 2; }
  chmod 600 "$ENV_FILE"
}
confirm() {
  local expected="$1"
  [[ "$CONFIRM" == "$expected" ]] || { echo "explicit confirmation required: $expected" >&2; exit 2; }
}
with_lock() {
  require_command flock
  mkdir -p "$(dirname "$LOCK_FILE")"
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "another platform operation is running" >&2; exit 2; }
}
preflight() {
  require_command docker
  require_env
  compose config --quiet
  for tool in python3 age sha256sum; do require_command "$tool"; done
  echo "PREFLIGHT: PASS"
}
snapshot_env() {
  mkdir -p "$RELEASE_DIR"
  chmod 700 "$RELEASE_DIR"
  local stamp snapshot
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  snapshot="$RELEASE_DIR/env-$stamp"
  [[ ! -e "$snapshot" ]] || { echo "snapshot already exists: $snapshot" >&2; exit 2; }
  cp -- "$ENV_FILE" "$snapshot"
  chmod 600 "$snapshot"
  printf '%s\n' "$snapshot"
}
case "$ACTION" in
  help|-h|--help) usage ;;
  preflight) preflight ;;
  status)
    require_command docker; require_env
    compose ps
    ;;
  health)
    require_command docker; require_env
    compose ps
    compose exec -T platform-api python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=5); print('readiness:', r.status)"
    ;;
  verify-backups)
    require_command python3
    python3 "$ROOT/scripts/verify_platform_backups.py" --backup-dir "$BACKUP_DIR"
    ;;
  upgrade-plan)
    : "${RELEASE_MANIFEST:?Set RELEASE_MANIFEST to a reviewed release manifest}"
    require_command python3
    python3 "$ROOT/scripts/release_upgrade.py" --manifest "$RELEASE_MANIFEST"
    ;;
  recovery-plan)
    require_command python3
    python3 "$ROOT/scripts/disaster_recovery.py" --plan
    ;;
  deploy)
    confirm CONFIRM-PRODUCTION-DEPLOY
    with_lock
    preflight
    snapshot="$(snapshot_env)"
    echo "Saved pre-deploy configuration: $snapshot"
    compose pull
    compose up -d --wait --remove-orphans
    compose ps
    compose exec -T platform-api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=5)"
    printf '%s\n' "$snapshot" > "$snapshot.success"
    chmod 600 "$snapshot.success"
    echo "DEPLOY: PASS"
    ;;
  backup)
    confirm CONFIRM-PLATFORM-BACKUP
    with_lock
    require_command bash
    : "${AGE_RECIPIENT:?Set AGE_RECIPIENT to the offline age recipient}"
    BACKUP_ENV_FILE="$ENV_FILE" BACKUP_DIR="$BACKUP_DIR" bash "$ROOT/scripts/backup_platform.sh"
    python3 "$ROOT/scripts/verify_platform_backups.py" --backup-dir "$BACKUP_DIR"
    ;;
  restore)
    confirm CONFIRM-PLATFORM-RESTORE
    with_lock
    require_command bash
    preflight
    bash "$ROOT/scripts/restore_platform.sh"
    ;;
  rollback)
    confirm CONFIRM-PRODUCTION-ROLLBACK
    with_lock
    require_command docker
    require_env
    : "${ROLLBACK_SNAPSHOT:?Set ROLLBACK_SNAPSHOT to a reviewed deploy/releases/env-* snapshot}"
    [[ -f "$ROLLBACK_SNAPSHOT" && ! -L "$ROLLBACK_SNAPSHOT" ]] || { echo "rollback snapshot missing or unsafe" >&2; exit 2; }
    case "$(realpath "$ROLLBACK_SNAPSHOT")" in
      "$(realpath "$RELEASE_DIR")"/env-*) ;;
      *) echo "rollback snapshot must be inside RELEASE_DIR" >&2; exit 2 ;;
    esac
    docker compose --env-file "$ROLLBACK_SNAPSHOT" -f "$COMPOSE" config --quiet
    current="$(snapshot_env)"
    cp "$ROLLBACK_SNAPSHOT" "$ENV_FILE.tmp"
    chmod 600 "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
    if ! compose up -d --wait --remove-orphans; then
      cp "$current" "$ENV_FILE.tmp"
      chmod 600 "$ENV_FILE.tmp"
      mv "$ENV_FILE.tmp" "$ENV_FILE"
      echo "rollback failed; previous configuration restored; manual service recovery required" >&2
      exit 1
    fi
    compose ps
    compose exec -T platform-api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=5)"
    echo "ROLLBACK: PASS; prior configuration snapshot: $current"
    ;;
  *) usage >&2; exit 2 ;;
esac
