#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$ROOT/deploy/compose.yaml"
ENV_FILE="${ENV_FILE:-$ROOT/deploy/.env}"
RELEASE_DIR="${RELEASE_DIR:-$ROOT/deploy/releases}"
: "${ROLLBACK_SNAPSHOT:?Set ROLLBACK_SNAPSHOT to a deploy/releases/env-* snapshot}"
[[ -f "$ENV_FILE" && -f "$ROLLBACK_SNAPSHOT" ]] || {
  echo "current env or rollback snapshot is missing" >&2
  exit 2
}
case "$(realpath "$ROLLBACK_SNAPSHOT")" in
  "$(realpath "$RELEASE_DIR")"/env-*) ;;
  *) echo "rollback snapshot must be inside deploy/releases" >&2; exit 2 ;;
esac
chmod 600 "$ROLLBACK_SNAPSHOT"
docker compose --env-file "$ROLLBACK_SNAPSHOT" -f "$COMPOSE" config --quiet
CURRENT="$RELEASE_DIR/pre-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RELEASE_DIR"
chmod 700 "$RELEASE_DIR"
cp "$ENV_FILE" "$CURRENT"
chmod 600 "$CURRENT"
cp "$ROLLBACK_SNAPSHOT" "$ENV_FILE.tmp"
chmod 600 "$ENV_FILE.tmp"
mv "$ENV_FILE.tmp" "$ENV_FILE"
if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE" up -d --wait --remove-orphans; then
  cp "$CURRENT" "$ENV_FILE.tmp"
  chmod 600 "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  echo "rollback failed; previous environment restored, inspect Docker health manually" >&2
  exit 1
fi
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" ps
printf 'Infrastructure configuration rolled back. Previous env snapshot: %s\n' "$CURRENT"
