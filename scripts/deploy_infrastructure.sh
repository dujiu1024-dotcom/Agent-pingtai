#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/compose.yaml"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/.env}"
RELEASE_DIR="${RELEASE_DIR:-$ROOT/deploy/releases}"
CHECK_ONLY=0
NO_PULL=0

usage() {
  cat <<'USAGE'
Usage: scripts/deploy_infrastructure.sh [--check] [--no-pull]
  --check    Validate required secrets and rendered Compose without changing services.
  --no-pull  Use already-cached images; skip registry pulls.
USAGE
}
while (($#)); do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --no-pull) NO_PULL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

command -v docker >/dev/null
[[ -f "$COMPOSE_FILE" ]] || { echo "Compose definition missing: $COMPOSE_FILE" >&2; exit 2; }
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "deployment env file missing or unsafe: $ENV_FILE" >&2
  exit 2
}
chmod 600 "$ENV_FILE"
if grep -Eq '^[A-Za-z_][A-Za-z0-9_]*=(REPLACE_WITH_[^[:space:]]*|CHANGEME|TODO|TBD|<[^>]*>)$' "$ENV_FILE"; then
  echo "deployment env file still contains placeholder secrets" >&2
  exit 2
fi

COMPOSE=(docker compose --project-directory "$ROOT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${COMPOSE[@]}" config --quiet
if ((CHECK_ONLY)); then
  echo "Deployment configuration is valid; no services were changed."
  exit 0
fi

mkdir -p "$RELEASE_DIR"
chmod 700 "$RELEASE_DIR"

# Only snapshots marked after a successful deployment are eligible for automatic rollback.
PREVIOUS_SUCCESS_MARKER="$(find "$RELEASE_DIR" -maxdepth 1 -type f -name 'env-*.success' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2- || true)"
PREVIOUS_SNAPSHOT=""
if [[ -n "$PREVIOUS_SUCCESS_MARKER" ]]; then
  IFS= read -r PREVIOUS_SNAPSHOT < "$PREVIOUS_SUCCESS_MARKER"
  case "$(realpath "$PREVIOUS_SNAPSHOT")" in
    "$(realpath "$RELEASE_DIR")"/env-*) ;;
    *) echo "successful deployment marker points outside release directory" >&2; exit 2 ;;
  esac
  [[ -f "$PREVIOUS_SNAPSHOT" && ! -L "$PREVIOUS_SNAPSHOT" ]] || {
    echo "previous successful deployment snapshot is missing or unsafe" >&2
    exit 2
  }
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPSHOT="$RELEASE_DIR/env-$STAMP-$$"
cp "$ENV_FILE" "$SNAPSHOT"
chmod 600 "$SNAPSHOT"
if ((!NO_PULL)); then
  "${COMPOSE[@]}" pull
fi
if ! "${COMPOSE[@]}" up -d --wait --remove-orphans; then
  echo "deployment failed; attempting rollback to previous successful infrastructure snapshot" >&2
  if [[ -z "$PREVIOUS_SNAPSHOT" ]]; then
    echo "no previously successful snapshot exists; manual recovery is required" >&2
    exit 1
  fi
  cp "$PREVIOUS_SNAPSHOT" "$ENV_FILE.tmp"
  chmod 600 "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  if ! "${COMPOSE[@]}" up -d --wait --remove-orphans; then
    echo "automatic rollback failed; previous environment file is restored but service health requires manual recovery" >&2
    exit 1
  fi
  echo "automatic rollback to previous successful infrastructure snapshot completed" >&2
  exit 1
fi
printf '%s\n' "$SNAPSHOT" > "$SNAPSHOT.success"
chmod 600 "$SNAPSHOT.success"
"${COMPOSE[@]}" ps
printf 'Infrastructure deployment completed. Rollback snapshot: %s\n' "$SNAPSHOT"
