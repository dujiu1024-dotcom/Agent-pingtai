#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RELEASE_ENV_FILE:-$ROOT/deploy/.env}"
COMPOSE_FILE="${RELEASE_COMPOSE_FILE:-$ROOT/deploy/compose.yaml}"
STATE_DIR="${RELEASE_STATE_DIR:-$ROOT/deploy/releases}"
MODE="${1:---check}"
usage() { echo "Usage: scripts/release_upgrade.sh [--check|--apply|--rollback]" >&2; }
case "$MODE" in --check|--apply|--rollback) ;; *) usage; exit 2 ;; esac
command -v docker >/dev/null
command -v sort >/dev/null
[[ -f "$COMPOSE_FILE" && -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo "Compose file or safe release env file is missing" >&2; exit 2; }
chmod 600 "$ENV_FILE"
# Parse dotenv-style entries without evaluating the file as shell code.
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || { echo "invalid release env entry" >&2; exit 2; }
  key="${BASH_REMATCH[1]}"
  value="${BASH_REMATCH[2]}"
  if [[ "$value" == \"*\" && "${#value}" -ge 2 ]]; then value="${value:1:${#value}-2}"; fi
  if [[ "$value" == \'*\' && "${#value}" -ge 2 ]]; then value="${value:1:${#value}-2}"; fi
  export "$key=$value"
done < "$ENV_FILE"
COMPOSE=(docker compose --project-directory "$ROOT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${COMPOSE[@]}" config --quiet
version_le() { [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n 1)" == "$1" ]]; }
validate_version() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || { echo "invalid semantic version: $1" >&2; exit 2; }; }
load_services() {
  IFS=',' read -r -a SERVICES <<< "${UPGRADE_SERVICES:?UPGRADE_SERVICES must be a comma-separated Compose service list}"
  (("${#SERVICES[@]}" > 0)) || { echo "UPGRADE_SERVICES is empty" >&2; exit 2; }
  for service in "${SERVICES[@]}"; do
    [[ "$service" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || { echo "invalid Compose service name: $service" >&2; exit 2; }
    grep -Fxq "$service" < <("${COMPOSE[@]}" config --services) || { echo "unknown Compose service: $service" >&2; exit 2; }
  done
}
snapshot_env() {
  mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR"
  local stamp snapshot
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  snapshot="$STATE_DIR/release-env-$stamp"
  cp "$ENV_FILE" "$snapshot"; chmod 600 "$snapshot"
  printf '%s\n' "$snapshot"
}
read_snapshot() {
  local pointer="$1" snapshot
  [[ -f "$pointer" && ! -L "$pointer" ]] || { echo "successful release snapshot pointer is missing or unsafe: $pointer" >&2; return 1; }
  IFS= read -r snapshot < "$pointer"
  case "$(realpath "$snapshot")" in "$(realpath "$STATE_DIR")"/release-env-*) ;; *) echo "release snapshot points outside state directory" >&2; return 1 ;; esac
  [[ -f "$snapshot" && ! -L "$snapshot" ]] || { echo "release snapshot is missing or unsafe" >&2; return 1; }
  printf '%s\n' "$snapshot"
}
restore_snapshot() {
  local snapshot="$1"
  cp "$snapshot" "$ENV_FILE.tmp"; chmod 600 "$ENV_FILE.tmp"; mv "$ENV_FILE.tmp" "$ENV_FILE"
}
restore_previous() {
  local snapshot
  snapshot="$(read_snapshot "$STATE_DIR/latest-success")" || return 1
  restore_snapshot "$snapshot"
}
publish_success() {
  local snapshot="$1" previous
  if [[ -f "$STATE_DIR/latest-success" && ! -L "$STATE_DIR/latest-success" ]]; then
    previous="$(read_snapshot "$STATE_DIR/latest-success")" || return 1
    printf '%s\n' "$previous" > "$STATE_DIR/previous-success.tmp"
    chmod 600 "$STATE_DIR/previous-success.tmp"
    mv "$STATE_DIR/previous-success.tmp" "$STATE_DIR/previous-success"
  fi
  printf '%s\n' "$snapshot" > "$STATE_DIR/latest-success.tmp"
  chmod 600 "$STATE_DIR/latest-success.tmp"
  mv "$STATE_DIR/latest-success.tmp" "$STATE_DIR/latest-success"
}
if [[ "$MODE" == "--rollback" ]]; then
  ROLLBACK_TARGET="$(read_snapshot "$STATE_DIR/previous-success")"
  CURRENT_SUCCESS="$(read_snapshot "$STATE_DIR/latest-success")"
  restore_snapshot "$ROLLBACK_TARGET"
  "${COMPOSE[@]}" config --quiet
  load_services
  for service in "${SERVICES[@]}"; do "${COMPOSE[@]}" up -d --no-deps --wait "$service"; done
  printf '%s\n' "$CURRENT_SUCCESS" > "$STATE_DIR/previous-success.tmp"
  chmod 600 "$STATE_DIR/previous-success.tmp"
  mv "$STATE_DIR/previous-success.tmp" "$STATE_DIR/previous-success"
  printf '%s\n' "$ROLLBACK_TARGET" > "$STATE_DIR/latest-success.tmp"
  chmod 600 "$STATE_DIR/latest-success.tmp"
  mv "$STATE_DIR/latest-success.tmp" "$STATE_DIR/latest-success"
  echo "Configuration restored and services rolled back; database migrations were not reversed."
  exit 0
fi
: "${CURRENT_RELEASE_VERSION:?CURRENT_RELEASE_VERSION must describe the running release}"
: "${RELEASE_VERSION:?RELEASE_VERSION must describe the target release}"
: "${RELEASE_COMPATIBILITY_MIN:?RELEASE_COMPATIBILITY_MIN is required}"
: "${RELEASE_COMPATIBILITY_MAX:?RELEASE_COMPATIBILITY_MAX is required}"
validate_version "$CURRENT_RELEASE_VERSION"; validate_version "$RELEASE_VERSION"
validate_version "$RELEASE_COMPATIBILITY_MIN"; validate_version "$RELEASE_COMPATIBILITY_MAX"
version_le "$RELEASE_COMPATIBILITY_MIN" "$RELEASE_COMPATIBILITY_MAX" || { echo "compatibility minimum exceeds maximum" >&2; exit 2; }
version_le "$RELEASE_COMPATIBILITY_MIN" "$CURRENT_RELEASE_VERSION" && version_le "$CURRENT_RELEASE_VERSION" "$RELEASE_COMPATIBILITY_MAX" || {
  echo "running release $CURRENT_RELEASE_VERSION is outside target compatibility window [$RELEASE_COMPATIBILITY_MIN, $RELEASE_COMPATIBILITY_MAX]" >&2; exit 3;
}
load_services
: "${PG_DSN:?PG_DSN is required for migration preflight}"
"$ROOT/scripts/check_migrations.sh"
if [[ "$MODE" == "--check" ]]; then echo "Release preflight passed for $CURRENT_RELEASE_VERSION -> $RELEASE_VERSION; no services changed."; exit 0; fi
: "${PRE_UPGRADE_BACKUP_COMMAND:?PRE_UPGRADE_BACKUP_COMMAND is required before applying an upgrade}"
# Do not begin an upgrade until a last-known-good release is available.
read_snapshot "$STATE_DIR/latest-success" >/dev/null
bash -euo pipefail -c "$PRE_UPGRADE_BACKUP_COMMAND"
SNAPSHOT="$(snapshot_env)"
if [[ -n "${MIGRATION_COMMAND:-}" ]] && ! bash -euo pipefail -c "$MIGRATION_COMMAND"; then
  echo "migration command failed; restoring prior release configuration (database changes, if any, require manual recovery)" >&2
  restore_previous || exit 1
  "${COMPOSE[@]}" config --quiet
  for rollback_service in "${SERVICES[@]}"; do
    "${COMPOSE[@]}" up -d --no-deps --wait "$rollback_service" || { echo "service recovery failed at $rollback_service; manual recovery required" >&2; exit 1; }
  done
  exit 1
fi
for service in "${SERVICES[@]}"; do
  if ! "${COMPOSE[@]}" up -d --no-deps --wait "$service"; then
    echo "upgrade failed at service $service; restoring prior configuration and rolling services back" >&2
    restore_previous || exit 1
    "${COMPOSE[@]}" config --quiet
    for rollback_service in "${SERVICES[@]}"; do
      "${COMPOSE[@]}" up -d --no-deps --wait "$rollback_service" || { echo "service rollback failed at $rollback_service; manual recovery required" >&2; exit 1; }
    done
    echo "configuration and service rollback completed; database migrations were not reversed" >&2
    exit 1
  fi
done
publish_success "$SNAPSHOT"
printf 'Release %s upgraded successfully.\n' "$RELEASE_VERSION"
