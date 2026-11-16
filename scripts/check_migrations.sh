#!/usr/bin/env bash
set -euo pipefail
: "${PG_DSN:?PG_DSN must be set}"
MIGRATION_DIR="${MIGRATION_DIR:-agent_platform/storage/postgres}"
[[ -d "$MIGRATION_DIR" ]] || { echo "migration directory not found: $MIGRATION_DIR" >&2; exit 2; }
command -v psql >/dev/null
command -v sha256sum >/dev/null
shopt -s nullglob
files=("$MIGRATION_DIR"/*.sql)
(("${#files[@]}" > 0)) || { echo "no SQL migrations found" >&2; exit 2; }
table_exists="$(psql "$PG_DSN" -XAtq -v ON_ERROR_STOP=1 -c "SELECT to_regclass('public.platform_schema_migrations') IS NOT NULL")"
declare -A applied=()
if [[ "$table_exists" == "t" ]]; then
  applied_rows="$(psql "$PG_DSN" -XAtq -F '|' -v ON_ERROR_STOP=1 -c "SELECT migration_name, checksum FROM public.platform_schema_migrations ORDER BY migration_name")"
  while IFS='|' read -r name checksum; do
    [[ -n "$name" ]] && applied["$name"]="$checksum"
  done <<< "$applied_rows"
fi
pending=0
for file in "${files[@]}"; do
  name="$(basename "$file")"
  [[ "$name" =~ ^[0-9]{3}_[A-Za-z0-9_]+\.sql$ ]] || { echo "invalid migration filename: $name" >&2; exit 2; }
  checksum="$(sha256sum "$file" | awk '{print $1}')"
  if [[ -v "applied[$name]" ]]; then
    [[ "${applied[$name]}" == "$checksum" ]] || { echo "checksum mismatch for applied migration: $name" >&2; exit 3; }
    printf 'applied %s\n' "$name"
  else
    pending=$((pending + 1))
    printf 'pending %s\n' "$name"
  fi
done
printf 'Migration preflight passed; pending migrations: %s\n' "$pending"
