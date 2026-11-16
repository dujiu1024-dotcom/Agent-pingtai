#!/usr/bin/env bash
set -euo pipefail

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT must be set}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY must be set}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY must be set}"
: "${MINIO_BUCKET:?MINIO_BUCKET must be set}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT must be set}"
command -v mc >/dev/null
command -v age >/dev/null
command -v sha256sum >/dev/null
command -v tar >/dev/null
command -v python3 >/dev/null

BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
WORK_DIR="$(mktemp -d)"
MC_CONFIG_DIR="$(mktemp -d)"
ARCHIVE_TMP="$(mktemp "$BACKUP_DIR/.objects-archive.XXXXXX")"
ENCRYPTED_TMP="$(mktemp "$BACKUP_DIR/.objects-encrypted.XXXXXX")"
MANIFEST_TMP="$(mktemp "$BACKUP_DIR/.objects-manifest.XXXXXX")"
cleanup() {
  rm -rf "$WORK_DIR" "$MC_CONFIG_DIR"
  rm -f "$ARCHIVE_TMP" "$ENCRYPTED_TMP" "$MANIFEST_TMP"
}
trap cleanup EXIT

export MC_CONFIG_DIR
mc alias set platform-source "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
mc ls "platform-source/$MINIO_BUCKET" >/dev/null
mc mirror --preserve --overwrite "platform-source/$MINIO_BUCKET" "$WORK_DIR/$MINIO_BUCKET"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
tar -C "$WORK_DIR" -czf "$ARCHIVE_TMP" "$MINIO_BUCKET"
age --encrypt --recipient "$AGE_RECIPIENT" --output "$ENCRYPTED_TMP" "$ARCHIVE_TMP"
BASE="$BACKUP_DIR/object-storage-$STAMP"
mv "$ENCRYPTED_TMP" "$BASE.tar.gz.age"
chmod 600 "$BASE.tar.gz.age"
(
  cd "$BACKUP_DIR"
  sha256sum "$(basename "$BASE.tar.gz.age")" > "$(basename "$BASE.tar.gz.age").sha256"
)
python3 - "$BASE.tar.gz.age" "$MINIO_BUCKET" "$MINIO_ENDPOINT" "$STAMP" > "$MANIFEST_TMP" <<'PY'
import hashlib, json, pathlib, sys
artifact, bucket, endpoint, stamp = sys.argv[1:]
path = pathlib.Path(artifact)
print(json.dumps({
    "format": "minio-mirror-age-v1",
    "artifact": path.name,
    "bucket": bucket,
    "endpoint": endpoint,
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "created_at_utc": stamp,
    "versioning_note": "mc mirror captures current object state; it does not preserve historical object versions",
}, ensure_ascii=False, sort_keys=True))
PY
mv "$MANIFEST_TMP" "$BASE.tar.gz.age.manifest.json"
chmod 600 "$BASE.tar.gz.age.sha256" "$BASE.tar.gz.age.manifest.json"
printf 'Encrypted object backup: %s.tar.gz.age\nChecksum: %s.tar.gz.age.sha256\nManifest: %s.tar.gz.age.manifest.json\n' "$BASE" "$BASE" "$BASE"
