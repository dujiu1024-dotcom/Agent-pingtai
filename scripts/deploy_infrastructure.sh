#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$ROOT/deploy/compose.yaml"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/.env}"
RELEASE_DIR="${RELEASE_DIR:-$ROOT/deploy/releases}"
command -v docker >/dev/null
[[ -f "$ENV_FILE" ]] || { echo "deployment env file missing: $ENV_FILE" >&2; exit 2; }
chmod 600 "$ENV_FILE"
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
SNAPSHOT="$RELEASE_DIR/env-$STAMP"
cp "$ENV_FILE" "$SNAPSHOT"
chmod 600 "$SNAPSHOT"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" config --quiet
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" pull
if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE" up -d --wait --remove-orphans; then
  echo "deployment failed; attempting rollback to previous successful infrastructure snapshot" >&2
  if [[ -z "$PREVIOUS_SNAPSHOT" ]]; then
    echo "no previously successful snapshot exists; manual recovery is required" >&2
    exit 1
  fi
  cp "$PREVIOUS_SNAPSHOT" "$ENV_FILE.tmp"
  chmod 600 "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE" up -d --wait --remove-orphans; then
    echo "automatic rollback failed; previous environment file is restored but service health requires manual recovery" >&2
    exit 1
  fi
  echo "automatic rollback to previous successful infrastructure snapshot completed" >&2
  exit 1
fi
printf '%s\n' "$SNAPSHOT" > "$SNAPSHOT.success"
chmod 600 "$SNAPSHOT.success"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" ps
printf 'Infrastructure deployment completed. Rollback snapshot: %s\n' "$SNAPSHOT"
