#!/usr/bin/env bash
set -euo pipefail

: "${AGE_IDENTITY:?AGE_IDENTITY must be set}"
: "${BACKUP_FILE:?BACKUP_FILE must point to .rdb.age}"
: "${REDIS_DATA_DIR:?REDIS_DATA_DIR must be set}"
: "${CONFIRM_REDIS_RESTORE:?Set CONFIRM_REDIS_RESTORE=RESTORE-PLATFORM-REDIS}"
: "${REDIS_STOPPED_CONFIRM:?Set REDIS_STOPPED_CONFIRM=YES only after stopping Redis}"
[[ "$CONFIRM_REDIS_RESTORE" == "RESTORE-PLATFORM-REDIS" ]] || {
  echo "Redis restore confirmation mismatch" >&2
  exit 2
}
[[ -f "$BACKUP_FILE" && -f "$BACKUP_FILE.sha256" && -f "$BACKUP_FILE.manifest.json" ]] || {
  echo "backup, checksum or manifest missing" >&2
  exit 2
}
command -v age >/dev/null
command -v redis-check-rdb >/dev/null
command -v sha256sum >/dev/null
command -v python3 >/dev/null
command -v stat >/dev/null
command -v chown >/dev/null
command -v realpath >/dev/null
[[ "$REDIS_STOPPED_CONFIRM" == "YES" ]] || { echo "Redis must be stopped before replacing its RDB" >&2; exit 2; }
[[ -d "$REDIS_DATA_DIR" && ! -L "$REDIS_DATA_DIR" ]] || {
  echo "REDIS_DATA_DIR must be an existing non-symlink directory" >&2
  exit 2
}
RDB_NAME="${REDIS_RDB_NAME:-dump.rdb}"
[[ "$RDB_NAME" != */* && "$RDB_NAME" != "." && "$RDB_NAME" != ".." ]] || {
  echo "REDIS_RDB_NAME must be a filename" >&2
  exit 2
}
TARGET="$REDIS_DATA_DIR/$RDB_NAME"
[[ ! -L "$TARGET" ]] || { echo "refusing to replace symlink RDB" >&2; exit 2; }
(
  cd "$(dirname "$BACKUP_FILE")"
  sha256sum --check "$(basename "$BACKUP_FILE").sha256"
)
python3 - "$BACKUP_FILE" "$BACKUP_FILE.manifest.json" <<'PY'
import hashlib, json, pathlib, sys
artifact = pathlib.Path(sys.argv[1])
manifest = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
if manifest.get("format") != "redis-rdb-age-v1":
    raise SystemExit("unsupported Redis backup format")
if manifest.get("artifact") != artifact.name:
    raise SystemExit("manifest artifact mismatch")
if hashlib.sha256(artifact.read_bytes()).hexdigest() != manifest.get("sha256"):
    raise SystemExit("manifest checksum mismatch")
PY

WORK_DIR="$(mktemp -d "$REDIS_DATA_DIR/.restore.XXXXXX")"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT
age --decrypt --identity "$AGE_IDENTITY" --output "$WORK_DIR/dump.rdb" "$BACKUP_FILE"
redis-check-rdb "$WORK_DIR/dump.rdb"
if [[ -f "$TARGET" ]]; then
  TARGET_OWNER="$(stat -c '%u:%g' "$TARGET")"
  TARGET_MODE="$(stat -c '%a' "$TARGET")"
  chown "$TARGET_OWNER" "$WORK_DIR/dump.rdb"
  chmod "$TARGET_MODE" "$WORK_DIR/dump.rdb"
  PREVIOUS="$TARGET.pre-restore.$(date -u +%Y%m%dT%H%M%SZ)"
  [[ ! -e "$PREVIOUS" ]] || { echo "pre-restore backup already exists: $PREVIOUS" >&2; exit 2; }
  cp -p "$TARGET" "$WORK_DIR/pre-restore.rdb"
  mv "$WORK_DIR/pre-restore.rdb" "$PREVIOUS"
else
  : "${REDIS_UID:?Set REDIS_UID when restoring into a data directory without an existing RDB}"
  : "${REDIS_GID:?Set REDIS_GID when restoring into a data directory without an existing RDB}"
  chown "$REDIS_UID:$REDIS_GID" "$WORK_DIR/dump.rdb"
  chmod "${REDIS_RDB_MODE:-600}" "$WORK_DIR/dump.rdb"
fi
mv "$WORK_DIR/dump.rdb" "$TARGET"
printf 'Redis RDB restored to %s. Restart Redis only after verifying file ownership and service configuration.\n' "$TARGET"
