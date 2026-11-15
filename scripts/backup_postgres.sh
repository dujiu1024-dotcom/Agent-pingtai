#!/usr/bin/env bash
set -euo pipefail

: "${PG_DSN:?PG_DSN must be set}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT must be set}"
command -v pg_dump >/dev/null
command -v age >/dev/null
command -v sha256sum >/dev/null

BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASE="$BACKUP_DIR/platform-$STAMP"
PLAIN="$(mktemp)"
trap 'rm -f "$PLAIN"' EXIT

pg_dump "$PG_DSN" --format=custom --no-owner --no-privileges --file="$PLAIN"
age --encrypt --recipient "$AGE_RECIPIENT" --output "$BASE.dump.age" "$PLAIN"
(
  cd "$BACKUP_DIR"
  sha256sum "$(basename "$BASE.dump.age")" > "$(basename "$BASE.dump.age").sha256"
)
chmod 600 "$BASE.dump.age" "$BASE.dump.age.sha256"
printf 'Encrypted backup: %s.dump.age\nChecksum: %s.dump.age.sha256\n' "$BASE" "$BASE"
