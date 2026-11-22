#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/deploy/.env"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT must be set}"
command -v age >/dev/null
command -v tar >/dev/null
command -v sha256sum >/dev/null
command -v python3 >/dev/null
command -v realpath >/dev/null

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "deployment env file missing or unsafe: $ENV_FILE" >&2
  exit 2
}
chmod 600 "$ENV_FILE"
mkdir -p "$BACKUP_DIR"
[[ -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] || {
  echo "backup directory must be a non-symlink directory" >&2
  exit 2
}
chmod 700 "$BACKUP_DIR"

for required in deploy/.env deploy/.env.example deploy/compose.yaml scripts/migrate_postgres.sh agent_platform/storage/postgres; do
  [[ -e "$ROOT/$required" && ! -L "$ROOT/$required" ]] || {
    echo "required configuration source missing or unsafe: $required" >&2
    exit 2
  }
done

WORK_DIR="$(mktemp -d "$BACKUP_DIR/.config-backup.XXXXXX")"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

# Include secrets only inside the encrypted archive; never publish a plaintext tar.
tar -C "$ROOT" -czf "$WORK_DIR/platform-config.tar.gz" \
  deploy/.env deploy/.env.example deploy/compose.yaml \
  scripts/migrate_postgres.sh agent_platform/storage/postgres

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASE="$BACKUP_DIR/platform-config-$STAMP-$$"
age --encrypt --recipient "$AGE_RECIPIENT" \
  --output "$WORK_DIR/platform-config.tar.gz.age" "$WORK_DIR/platform-config.tar.gz"
[[ -s "$WORK_DIR/platform-config.tar.gz.age" ]] || {
  echo "age produced an empty encrypted configuration archive" >&2
  exit 2
}

mv "$WORK_DIR/platform-config.tar.gz.age" "$BASE.tar.gz.age"
chmod 600 "$BASE.tar.gz.age"
(
  cd "$BACKUP_DIR"
  sha256sum "$(basename "$BASE.tar.gz.age")" > "$WORK_DIR/checksum"
)
mv "$WORK_DIR/checksum" "$BASE.tar.gz.age.sha256"

python3 - "$BASE.tar.gz.age" "$STAMP" > "$WORK_DIR/manifest.json" <<'PY'
import hashlib, json, pathlib, sys
artifact, stamp = sys.argv[1:]
path = pathlib.Path(artifact)
digest = hashlib.sha256()
with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
print(json.dumps({
    "format": "platform-config-tar-age-v1",
    "artifact": path.name,
    "sha256": digest.hexdigest(),
    "created_at_utc": stamp,
    "contents": [
        "deploy/.env",
        "deploy/.env.example",
        "deploy/compose.yaml",
        "scripts/migrate_postgres.sh",
        "agent_platform/storage/postgres/",
    ],
    "secrets_included": True,
    "encryption": "age recipient encryption",
}, ensure_ascii=False, sort_keys=True))
PY
mv "$WORK_DIR/manifest.json" "$BASE.tar.gz.age.manifest.json"
chmod 600 "$BASE.tar.gz.age.sha256" "$BASE.tar.gz.age.manifest.json"
printf 'Encrypted configuration backup: %s.tar.gz.age\nChecksum: %s.tar.gz.age.sha256\nManifest: %s.tar.gz.age.manifest.json\n' "$BASE" "$BASE" "$BASE"
