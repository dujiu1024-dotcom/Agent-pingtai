#!/usr/bin/env bash
set -euo pipefail

: "${PG_DSN:?PG_DSN must be set}"
: "${AGE_IDENTITY:?AGE_IDENTITY must be set}"
: "${BACKUP_FILE:?BACKUP_FILE must point to .dump.age}"
: "${CONFIRM_RESTORE:?Set CONFIRM_RESTORE=RESTORE-PLATFORM-DATABASE to continue}"
[[ "$CONFIRM_RESTORE" == "RESTORE-PLATFORM-DATABASE" ]] || { echo "restore confirmation mismatch" >&2; exit 2; }
[[ -f "$BACKUP_FILE" && -f "$BACKUP_FILE.sha256" ]] || { echo "backup or checksum file missing" >&2; exit 2; }
command -v age >/dev/null
command -v pg_restore >/dev/null
command -v sha256sum >/dev/null

(
  cd "$(dirname "$BACKUP_FILE")"
  sha256sum --check "$(basename "$BACKUP_FILE").sha256"
)
PLAIN="$(mktemp)"
trap 'rm -f "$PLAIN"' EXIT
age --decrypt --identity "$AGE_IDENTITY" --output "$PLAIN" "$BACKUP_FILE"
pg_restore --dbname="$PG_DSN" --clean --if-exists --no-owner --no-privileges "$PLAIN"
