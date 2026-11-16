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
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPSHOT="$RELEASE_DIR/env-$STAMP"
cp "$ENV_FILE" "$SNAPSHOT"
chmod 600 "$SNAPSHOT"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" config --quiet
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" pull
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" up -d --wait --remove-orphans
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" ps
printf 'Infrastructure deployment completed. Rollback snapshot: %s\n' "$SNAPSHOT"
