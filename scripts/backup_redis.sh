#!/usr/bin/env bash
set -euo pipefail

: "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT must be set}"
command -v redis-cli >/dev/null
command -v age >/dev/null
command -v sha256sum >/dev/null
command -v python3 >/dev/null

BACKUP_DIR="${BACKUP_DIR:-./backups}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
WORK_DIR="$(mktemp -d)"
ENCRYPTED_TMP="$(mktemp "$BACKUP_DIR/.redis-encrypted.XXXXXX")"
MANIFEST_TMP="$(mktemp "$BACKUP_DIR/.redis-manifest.XXXXXX")"
cleanup() {
  rm -rf "$WORK_DIR"
  rm -f "$ENCRYPTED_TMP" "$MANIFEST_TMP"
}
trap cleanup EXIT

export REDISCLI_AUTH="$REDIS_PASSWORD"
REDIS_ARGS=(-h "$REDIS_HOST" -p "$REDIS_PORT")
if [[ -n "${REDIS_USERNAME:-}" ]]; then REDIS_ARGS+=(--user "$REDIS_USERNAME"); fi
if [[ "${REDIS_TLS:-0}" == "1" ]]; then REDIS_ARGS+=(--tls); fi
redis-cli "${REDIS_ARGS[@]}" --rdb "$WORK_DIR/dump.rdb"
[[ -s "$WORK_DIR/dump.rdb" ]] || { echo "Redis produced an empty RDB snapshot" >&2; exit 2; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASE="$BACKUP_DIR/redis-$STAMP"
age --encrypt --recipient "$AGE_RECIPIENT" --output "$ENCRYPTED_TMP" "$WORK_DIR/dump.rdb"
mv "$ENCRYPTED_TMP" "$BASE.rdb.age"
chmod 600 "$BASE.rdb.age"
(
  cd "$BACKUP_DIR"
  sha256sum "$(basename "$BASE.rdb.age")" > "$(basename "$BASE.rdb.age").sha256"
)
python3 - "$BASE.rdb.age" "$REDIS_HOST" "$REDIS_PORT" "$STAMP" > "$MANIFEST_TMP" <<'PY'
import hashlib, json, pathlib, sys
artifact, host, port, stamp = sys.argv[1:]
path = pathlib.Path(artifact)
print(json.dumps({
    "format": "redis-rdb-age-v1",
    "artifact": path.name,
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "source_host": host,
    "source_port": int(port),
    "created_at_utc": stamp,
    "snapshot": "Redis RDB; point-in-time dataset, not an AOF history archive",
}, sort_keys=True))
PY
mv "$MANIFEST_TMP" "$BASE.rdb.age.manifest.json"
chmod 600 "$BASE.rdb.age.sha256" "$BASE.rdb.age.manifest.json"
printf 'Encrypted Redis snapshot: %s.rdb.age\nChecksum: %s.rdb.age.sha256\nManifest: %s.rdb.age.manifest.json\n' "$BASE" "$BASE" "$BASE"
