#!/usr/bin/env bash
set -euo pipefail

# Deliberately orchestrates existing component restore scripts only. It never
# provisions infrastructure, stops services, promotes replicas, or performs failover.

: "${RESTORE_ORDER_CONFIRM:?Set RESTORE_ORDER_CONFIRM=RESTORE-PLATFORM-IN-ORDER after reviewing the runbook}"
[[ "$RESTORE_ORDER_CONFIRM" == "RESTORE-PLATFORM-IN-ORDER" ]] || {
  echo "restore order confirmation mismatch" >&2
  exit 2
}
: "${BACKUP_DIR:?BACKUP_DIR must point to the verified backup directory}"
[[ -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] || {
  echo "BACKUP_DIR must be an existing non-symlink directory" >&2
  exit 2
}
[[ -f "$BACKUP_DIR/RESTORE-READY" ]] || {
  echo "Create RESTORE-READY only after stopping application writers and reviewing the runbook" >&2
  exit 2
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for component_script in restore_postgres.sh restore_redis.sh restore_object_storage.sh; do
  [[ -f "$SCRIPT_DIR/$component_script" ]] || {
    echo "required executable is missing: $SCRIPT_DIR/$component_script" >&2
    exit 2
  }
done

# The order is intentional: database state first, cache second, object payloads
# last. Application services must remain stopped until all post-restore checks pass.
: "${PG_BACKUP_FILE:?PG_BACKUP_FILE must identify the PostgreSQL .dump.age artifact}"
: "${REDIS_BACKUP_FILE:?REDIS_BACKUP_FILE must identify the Redis .rdb.age artifact}"
: "${OBJECT_BACKUP_FILE:?OBJECT_BACKUP_FILE must identify the object .tar.gz.age artifact}"
for artifact in "$PG_BACKUP_FILE" "$REDIS_BACKUP_FILE" "$OBJECT_BACKUP_FILE"; do
  [[ -f "$artifact" && -f "$artifact.sha256" && -f "$artifact.manifest.json" ]] || {
    echo "backup artifact, checksum or manifest missing: $artifact" >&2
    exit 2
  }
done

export BACKUP_FILE="$PG_BACKUP_FILE"
bash "$SCRIPT_DIR/restore_postgres.sh"

export BACKUP_FILE="$REDIS_BACKUP_FILE"
bash "$SCRIPT_DIR/restore_redis.sh"

export BACKUP_FILE="$OBJECT_BACKUP_FILE"
bash "$SCRIPT_DIR/restore_object_storage.sh"

printf 'Component restores completed. Keep application writers stopped until the runbook validation checklist passes.\n'
