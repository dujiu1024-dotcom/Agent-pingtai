#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
BACKUP_POLICY_FILE="${BACKUP_POLICY_FILE:-$ROOT/deploy/backup-policy.json}"
BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-$ROOT/deploy/.env}"
BACKUP_COMPONENTS="${BACKUP_COMPONENTS:-postgres,redis,objects,config}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT must be set to the offline age recipient}"
command -v age >/dev/null
command -v sha256sum >/dev/null
command -v python3 >/dev/null
[[ -f "$BACKUP_POLICY_FILE" && ! -L "$BACKUP_POLICY_FILE" ]] || { echo "backup policy file missing or unsafe" >&2; exit 2; }
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
export BACKUP_DIR AGE_RECIPIENT

TEMP_FILES=()
cleanup_temp_files() { ((${#TEMP_FILES[@]} == 0)) || rm -f -- "${TEMP_FILES[@]}"; }
trap cleanup_temp_files EXIT

backup_config() {
  [[ -f "$BACKUP_ENV_FILE" && ! -L "$BACKUP_ENV_FILE" ]] || {
    echo "BACKUP_ENV_FILE must point to an existing non-symlink deployment env file" >&2
    return 2
  }
  local env_real env_relative stamp base archive_tmp manifest_tmp encrypted_tmp
  env_real="$(realpath "$BACKUP_ENV_FILE")"
  case "$env_real" in "$ROOT"/*) ;; *) echo "deployment env file must be inside repository root" >&2; return 2 ;; esac
  env_relative="${env_real#"$ROOT"/}"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  base="$BACKUP_DIR/platform-config-$stamp"
  archive_tmp="$(mktemp "$BACKUP_DIR/.config-archive.XXXXXX")"
  manifest_tmp="$(mktemp "$BACKUP_DIR/.config-manifest.XXXXXX")"
  encrypted_tmp="$(mktemp "$BACKUP_DIR/.config-encrypted.XXXXXX")"
  TEMP_FILES+=("$archive_tmp" "$manifest_tmp" "$encrypted_tmp")
  tar -C "$ROOT" -czf "$archive_tmp" deploy/compose.yaml "$env_relative" deploy/backup-policy.json scripts/migrate_postgres.sh agent_platform/storage/postgres
  age --encrypt --recipient "$AGE_RECIPIENT" --output "$encrypted_tmp" "$archive_tmp"
  mv "$encrypted_tmp" "$base.tar.gz.age"
  chmod 600 "$base.tar.gz.age"
  (cd "$BACKUP_DIR" && sha256sum "$(basename "$base.tar.gz.age")" > "$(basename "$base.tar.gz.age").sha256")
  python3 - "$base.tar.gz.age" "$env_relative" "$stamp" > "$manifest_tmp" <<'PY'
import hashlib, json, pathlib, sys
artifact, env_path, stamp = sys.argv[1:]
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
    "included_env_path": env_path,
    "contents": ["deploy/compose.yaml", env_path, "deploy/backup-policy.json",
                 "scripts/migrate_postgres.sh", "agent_platform/storage/postgres/*.sql"],
    "encryption": "age recipient encryption; keep matching identity offline",
}, sort_keys=True))
PY
  mv "$manifest_tmp" "$base.tar.gz.age.manifest.json"
  chmod 600 "$base.tar.gz.age.sha256" "$base.tar.gz.age.manifest.json"
  printf 'Encrypted configuration backup: %s.tar.gz.age\n' "$base"
}

run_component() {
  case "$1" in
    postgres) "$ROOT/scripts/backup_postgres.sh" ;;
    redis) "$ROOT/scripts/backup_redis.sh" ;;
    objects) "$ROOT/scripts/backup_object_storage.sh" ;;
    config) backup_config ;;
    *) echo "unsupported backup component: $1" >&2; return 2 ;;
  esac
}

apply_retention() {
  python3 - "$BACKUP_DIR" "$BACKUP_POLICY_FILE" <<'PY'
import json, pathlib, sys, time
root = pathlib.Path(sys.argv[1]).resolve()
policy = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
now = time.time()
for component, item in policy["components"].items():
    days = int(item["retention_days"])
    prefix = item["artifact_prefix"]
    for artifact in root.glob(prefix + "*.age"):
        if artifact.is_symlink() or not artifact.is_file():
            continue
        if artifact.stat().st_mtime < now - days * 86400:
            for sidecar in (artifact, pathlib.Path(str(artifact) + ".sha256"),
                            pathlib.Path(str(artifact) + ".manifest.json")):
                if sidecar.is_file() and not sidecar.is_symlink():
                    sidecar.unlink()
            print(f"expired {component} backup: {artifact.name}")
PY
}

IFS=',' read -r -a components <<< "$BACKUP_COMPONENTS"
(("${#components[@]}" > 0)) || { echo "no backup components selected" >&2; exit 2; }
for component in "${components[@]}"; do
  component="$(printf '%s' "$component" | tr -d '[:space:]')"
  [[ -n "$component" ]] || continue
  echo "Starting $component backup"
  run_component "$component"
done
apply_retention
echo "Platform backup run completed. Components: $BACKUP_COMPONENTS"
