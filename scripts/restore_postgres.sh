#!/usr/bin/env bash
set -euo pipefail

: "${PG_DSN:?PG_DSN must be set}"
: "${AGE_IDENTITY:?AGE_IDENTITY must be set}"
: "${BACKUP_FILE:?BACKUP_FILE must point to .dump.age}"
: "${CONFIRM_RESTORE:?Set CONFIRM_RESTORE=RESTORE-PLATFORM-DATABASE to continue}"
[[ "$CONFIRM_RESTORE" == "RESTORE-PLATFORM-DATABASE" ]] || { echo "restore confirmation mismatch" >&2; exit 2; }
MANIFEST="$BACKUP_FILE.manifest.json"
[[ -f "$BACKUP_FILE" && -f "$BACKUP_FILE.sha256" && -f "$MANIFEST" ]] || { echo "backup, checksum or manifest missing" >&2; exit 2; }
command -v age >/dev/null
command -v pg_restore >/dev/null
command -v psql >/dev/null
command -v sha256sum >/dev/null
command -v python3 >/dev/null

(
  cd "$(dirname "$BACKUP_FILE")"
  sha256sum --check "$(basename "$BACKUP_FILE").sha256"
)
python3 - "$BACKUP_FILE" "$MANIFEST" <<'PY'
import hashlib, json, pathlib, sys
artifact, manifest_path = map(pathlib.Path, sys.argv[1:])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
digest = hashlib.sha256()
with artifact.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
if manifest.get("format") != "postgres-custom-age-v1":
    raise SystemExit("unsupported backup manifest format")
if manifest.get("artifact") != artifact.name:
    raise SystemExit("manifest artifact name mismatch")
if digest.hexdigest() != manifest.get("sha256"):
    raise SystemExit("manifest checksum mismatch")
PY

SOURCE_DB="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["database"])' "$MANIFEST")"
TARGET_DB="$(psql "$PG_DSN" -X -A -t -v ON_ERROR_STOP=1 -c 'SELECT current_database()')"
if [[ "$SOURCE_DB" != "$TARGET_DB" && "${ALLOW_DIFFERENT_DATABASE:-0}" != "1" ]]; then
  echo "target database differs from backup; set ALLOW_DIFFERENT_DATABASE=1 after review" >&2
  exit 2
fi

PLAIN="$(mktemp)"
trap 'rm -f "$PLAIN"' EXIT
age --decrypt --identity "$AGE_IDENTITY" --output "$PLAIN" "$BACKUP_FILE"
pg_restore --list "$PLAIN" >/dev/null
pg_restore --dbname="$PG_DSN" --exit-on-error --single-transaction --clean --if-exists --no-owner --no-privileges "$PLAIN"
printf 'Restore completed into database: %s\n' "$TARGET_DB"
