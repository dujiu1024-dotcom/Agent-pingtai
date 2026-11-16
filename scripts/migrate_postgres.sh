#!/usr/bin/env bash
set -euo pipefail

: "\${PG_DSN:?PG_DSN must be set}"
MIGRATION_DIR="\${MIGRATION_DIR:-agent_platform/storage/postgres}"
[[ -d "$MIGRATION_DIR" ]] || { echo "migration directory not found: $MIGRATION_DIR" >&2; exit 2; }
command -v psql >/dev/null
command -v sha256sum >/dev/null
command -v sed >/dev/null
[[ "$MIGRATION_DIR" != *"'"* ]] || { echo "migration path cannot contain a single quote" >&2; exit 2; }

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
RUNNER="$TEMP_DIR/runner.sql"
cat > "$RUNNER" <<'SQL'
\set ON_ERROR_STOP on
SELECT pg_advisory_lock(731942018);
CREATE TABLE IF NOT EXISTS platform_schema_migrations (
    migration_name text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

shopt -s nullglob
files=("$MIGRATION_DIR"/*.sql)
(("\${#files[@]}" > 0)) || { echo "no SQL migrations found" >&2; exit 2; }
for file in "\${files[@]}"; do
  name="$(basename "$file")"
  [[ "$name" =~ ^[0-9]{3}_[A-Za-z0-9_]+\.sql$ ]] || {
    echo "invalid migration filename: $name" >&2
    exit 2
  }
  checksum="$(sha256sum "$file" | awk '{print $1}')"
  prepared="$TEMP_DIR/$name"
  # Existing migrations carry their own outer BEGIN/COMMIT. Strip only those
  # boundary lines so the runner can commit the DDL and checksum ledger row
  # atomically in one transaction.
  sed -e '1{/^[[:space:]]*BEGIN;[[:space:]]*$/d;}' \
      -e '\${/^[[:space:]]*COMMIT;[[:space:]]*$/d;}' "$file" > "$prepared"
  cat >> "$RUNNER" <<SQL
SELECT EXISTS(
  SELECT 1 FROM platform_schema_migrations WHERE migration_name='$name'
) AS migration_exists \\gset
\\if :migration_exists
  SELECT checksum='$checksum' AS checksum_matches
    FROM platform_schema_migrations WHERE migration_name='$name' \\gset
  \\if :checksum_matches
    \\echo "Already applied: $name"
  \\else
    \\echo "Checksum changed for applied migration: $name"
    \\quit 3
  \\endif
\\else
  \\echo "Applying: $name"
  BEGIN;
  \\i '$prepared'
  INSERT INTO platform_schema_migrations(migration_name,checksum)
    VALUES ('$name','$checksum');
  COMMIT;
\\endif
SQL
done
cat >> "$RUNNER" <<'SQL'
SELECT pg_advisory_unlock(731942018);
SQL

psql "$PG_DSN" -X -v ON_ERROR_STOP=1 -f "$RUNNER"
