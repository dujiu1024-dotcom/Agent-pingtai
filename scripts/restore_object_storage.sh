#!/usr/bin/env bash
set -euo pipefail

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT must be set}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY must be set}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY must be set}"
: "${MINIO_BUCKET:?MINIO_BUCKET must be set}"
: "${AGE_IDENTITY:?AGE_IDENTITY must be set}"
: "${BACKUP_FILE:?BACKUP_FILE must point to .tar.gz.age}"
: "${CONFIRM_OBJECT_RESTORE:?Set CONFIRM_OBJECT_RESTORE=RESTORE-PLATFORM-OBJECTS}"
[[ "$CONFIRM_OBJECT_RESTORE" == "RESTORE-PLATFORM-OBJECTS" ]] || {
  echo "object restore confirmation mismatch" >&2
  exit 2
}
MANIFEST="$BACKUP_FILE.manifest.json"
[[ -f "$BACKUP_FILE" && -f "$BACKUP_FILE.sha256" && -f "$MANIFEST" ]] || {
  echo "backup, checksum or manifest missing" >&2
  exit 2
}
command -v mc >/dev/null
command -v age >/dev/null
command -v sha256sum >/dev/null
command -v python3 >/dev/null

(
  cd "$(dirname "$BACKUP_FILE")"
  sha256sum --check "$(basename "$BACKUP_FILE").sha256"
)
python3 - "$BACKUP_FILE" "$MANIFEST" "$MINIO_BUCKET" <<'PY'
import hashlib, json, pathlib, sys
artifact, manifest_path, bucket = sys.argv[1:]
manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
if manifest.get("format") != "minio-mirror-age-v1":
    raise SystemExit("unsupported object backup format")
if manifest.get("artifact") != pathlib.Path(artifact).name:
    raise SystemExit("manifest artifact mismatch")
if manifest.get("bucket") != bucket:
    raise SystemExit("backup bucket does not match restore target")
if hashlib.sha256(pathlib.Path(artifact).read_bytes()).hexdigest() != manifest.get("sha256"):
    raise SystemExit("manifest checksum mismatch")
PY

WORK_DIR="$(mktemp -d)"
MC_CONFIG_DIR="$(mktemp -d)"
ARCHIVE_TMP="$(mktemp)"
cleanup() {
  rm -rf "$WORK_DIR" "$MC_CONFIG_DIR"
  rm -f "$ARCHIVE_TMP"
}
trap cleanup EXIT
export MC_CONFIG_DIR
age --decrypt --identity "$AGE_IDENTITY" --output "$ARCHIVE_TMP" "$BACKUP_FILE"
python3 - "$ARCHIVE_TMP" "$WORK_DIR" "$MINIO_BUCKET" <<'PY'
import pathlib, shutil, sys, tarfile
archive, destination, expected_bucket = sys.argv[1:]
root = pathlib.Path(destination).resolve()
seen = set()
with tarfile.open(archive, "r:gz") as source:
    for member in source.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise SystemExit("unsafe archive path")
        if path.parts[0] != expected_bucket:
            raise SystemExit("archive contains an unexpected bucket")
        if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
            raise SystemExit("archive contains unsupported filesystem entry")
        target = root.joinpath(*path.parts)
        if not target.resolve().is_relative_to(root):
            raise SystemExit("archive path escapes restore directory")
        if member.name in seen:
            raise SystemExit("archive contains duplicate paths")
        seen.add(member.name)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        stream = source.extractfile(member)
        if stream is None:
            raise SystemExit("archive file content is missing")
        with target.open("xb") as output:
            shutil.copyfileobj(stream, output)
PY

mc alias set platform-target "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
mc ls "platform-target/$MINIO_BUCKET" >/dev/null
if [[ "${OBJECT_RESTORE_DELETE_EXTRA:-0}" == "1" ]]; then
  echo "WARNING: removing destination objects absent from the backup"
  mc mirror --preserve --overwrite --remove "$WORK_DIR/$MINIO_BUCKET" "platform-target/$MINIO_BUCKET"
else
  mc mirror --preserve --overwrite "$WORK_DIR/$MINIO_BUCKET" "platform-target/$MINIO_BUCKET"
fi
printf 'Object restore completed for bucket: %s\n' "$MINIO_BUCKET"
