#!/usr/bin/env bash
set -euo pipefail

: "${PG_DSN:?PG_DSN must be set}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT must be set}"
command -v pg_dump >/dev/null
command -v psql >/dev/null
command -v age >/dev/null
command -v sha256sum >/dev/null
command -v python3 >/dev/null

BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASE="$BACKUP_DIR/platform-$STAMP"
PLAIN="$(mktemp "$BACKUP_DIR/.platform-plain.XXXXXX")"
ENCRYPTED_TMP="$(mktemp "$BACKUP_DIR/.platform-encrypted.XXXXXX")"
MANIFEST_TMP="$(mktemp "$BACKUP_DIR/.platform-manifest.XXXXXX")"
trap 'rm -f "$PLAIN" "$ENCRYPTED_TMP" "$MANIFEST_TMP"' EXIT

DB_NAME="$(psql "$PG_DSN" -X -A -t -v ON_ERROR_STOP=1 -c 'SELECT current_database()')"
SERVER_VERSION="$(psql "$PG_DSN" -X -A -t -v ON_ERROR_STOP=1 -c 'SHOW server_version')"
DUMP_VERSION="$(pg_dump --version)"
pg_dump "$PG_DSN" --format=custom --no-owner --no-privileges --file="$PLAIN"
age --encrypt --recipient "$AGE_RECIPIENT" --output "$ENCRYPTED_TMP" "$PLAIN"
mv "$ENCRYPTED_TMP" "$BASE.dump.age"
chmod 600 "$BASE.dump.age"

(
  cd "$BACKUP_DIR"
  sha256sum "$(basename "$BASE.dump.age")" > "$(basename "$BASE.dump.age").sha256"
)
python3 - "$BASE.dump.age" "$DB_NAME" "$SERVER_VERSION" "$DUMP_VERSION" "$STAMP" > "$MANIFEST_TMP" <<'PY'
import hashlib, json, pathlib, sys
artifact, database, server_version, dump_version, stamp = sys.argv[1:]
path = pathlib.Path(artifact)
print(json.dumps({
    "format": "postgres-custom-age-v1",
    "artifact": path.name,
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "database": database,
    "server_version": server_version,
    "pg_dump_version": dump_version,
    "created_at_utc": stamp,
}, ensure_ascii=False, sort_keys=True))
PY
mv "$MANIFEST_TMP" "$BASE.dump.age.manifest.json"
chmod 600 "$BASE.dump.age.sha256" "$BASE.dump.age.manifest.json"
printf 'Encrypted backup: %s.dump.age\nChecksum: %s.dump.age.sha256\nManifest: %s.dump.age.manifest.json\n' "$BASE" "$BASE" "$BASE"
