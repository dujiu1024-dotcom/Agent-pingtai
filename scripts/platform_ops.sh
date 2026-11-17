#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${COMPOSE_FILE:-$ROOT/deploy/compose.yaml}"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/.env}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"

usage() {
  cat <<'EOF'
Usage: scripts/platform_ops.sh <command> [component]

Commands:
  deploy       Validate and deploy the pinned infrastructure/application stack.
  upgrade      Pull configured image versions and perform a health-gated rollout.
  backup       Back up PostgreSQL, Redis and MinIO/object storage.
  restore      Restore one component: postgres | redis | object-storage.
  rollback     Roll back Compose configuration to ROLLBACK_SNAPSHOT.
  verify       Validate Compose configuration and report service health.
  diagnose     Print safe service/status diagnostics (does not print logs or secrets).
  help         Show this help.

Required credentials are supplied through the environment or deploy/.env.
Restore commands additionally require each component script's explicit confirmation.
EOF
}

need_file() {
  [[ -f "$1" ]] || { echo "required file not found: $1" >&2; exit 2; }
}

compose() {
  need_file "$COMPOSE"
  need_file "$ENV_FILE"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE" "$@"
}

case "${1:-help}" in
  help|-h|--help) usage ;;
  deploy)
    compose config --quiet
    exec "$ROOT/scripts/deploy_infrastructure.sh"
    ;;
  upgrade)
    # Image tags/digests must be pinned in the deployment environment; never use :latest.
    if grep -Eq 'image:.*:latest([[:space:]]|$)' "$COMPOSE"; then
      echo "refusing upgrade: floating :latest image tag found in $COMPOSE" >&2
      exit 2
    fi
    compose config --quiet
    exec "$ROOT/scripts/deploy_infrastructure.sh"
    ;;
  backup)
    # A successful component command is not enough: require a fresh artifact,
    # matching checksum/manifest, and format verification before returning success.
    python3 "$ROOT/scripts/run_platform_backup.py" --backup-dir "$BACKUP_DIR"
    ;;
  restore)
    component="${2:-}"
    case "$component" in
      postgres) exec "$ROOT/scripts/restore_postgres.sh" ;;
      redis) exec "$ROOT/scripts/restore_redis.sh" ;;
      object-storage) exec "$ROOT/scripts/restore_object_storage.sh" ;;
      *) echo "restore requires postgres, redis, or object-storage" >&2; usage >&2; exit 2 ;;
    esac
    ;;
  rollback)
    exec "$ROOT/scripts/rollback_infrastructure.sh"
    ;;
  verify)
    compose config --quiet
    python3 - "$COMPOSE" "$ENV_FILE" <<'PY'
import json, subprocess, sys
compose_file, env_file = sys.argv[1:]
result = subprocess.run(
    ["docker", "compose", "--env-file", env_file, "-f", compose_file,
     "ps", "--format", "json"],
    check=True, capture_output=True, text=True,
)
raw = result.stdout.strip()
if not raw:
    raise SystemExit("no Compose services found")
try:
    payload = json.loads(raw)
    services = payload if isinstance(payload, list) else [payload]
except json.JSONDecodeError:
    services = [json.loads(line) for line in raw.splitlines() if line.strip()]
failed = []
for service in services:
    name = service.get("Service") or service.get("Name") or "unknown"
    state = str(service.get("State", "")).lower()
    health = str(service.get("Health", "")).lower()
    print(f"{name}: state={state or 'unknown'} health={health or 'not-reported'}")
    if state not in {"running"} or health in {"unhealthy", "starting"}:
        failed.append(name)
if failed:
    raise SystemExit("unhealthy or non-running services: " + ", ".join(failed))
print(f"Compose health check passed ({len(services)} services)")
PY
    ;;
  diagnose)
    compose ps
    echo
    echo "Compose configuration: valid"
    echo "Credential values and container logs are intentionally not printed."
    ;;
  *)
    echo "unknown command: $1" >&2
    usage >&2
    exit 2
    ;;
esac
