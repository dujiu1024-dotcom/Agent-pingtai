#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: PG_DSN=... $0 <migration-name.sql>" >&2
  echo "Requires <migration-name.sql.down.sql> in MIGRATION_DIR." >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
: "${PG_DSN:?PG_DSN must be set}"
MIGRATION_DIR="${MIGRATION_DIR:-agent_platform/storage/postgres}"
migration_name="$1"

# Match the upgrade runner's filename contract and use the complete filename,
# never the numeric prefix, because historical prefixes are not unique.
[[ "$migration_name" =~ ^[0-9]{3}_[A-Za-z0-9_]+\.sql$ ]] || usage
[[ -d "$MIGRATION_DIR" ]] || { echo "migration directory not found: $MIGRATION_DIR" >&2; exit 2; }
[[ "$MIGRATION_DIR" != *"'"* ]] || { echo "migration path cannot contain a single quote" >&2; exit 2; }

down_file="$MIGRATION_DIR/$migration_name.down.sql"
[[ -f "$down_file" && ! -L "$down_file" ]] || {
  echo "down migration not found: $down_file" >&2
  exit 2
}
command -v psql >/dev/null

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
RUNNER="$TEMP_DIR/downgrade.sql"
cat > "$RUNNER" <<'SQL'
\set ON_ERROR_STOP on
SELECT pg_advisory_lock(731942018);
CREATE TABLE IF NOT EXISTS platform_schema_migrations (
    migration_name text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

prepared="$TEMP_DIR/$migration_name.down"
# Match the upgrade runner: remove only top-level transaction boundary lines.
awk '
  function update_dollar_state(line, rest, token) {
    rest = line
    while (match(rest, /\$[[:alnum:]_]*\$/)) {
      token = substr(rest, RSTART, RLENGTH)
      if (dollar_tag == "") dollar_tag = token
      else if (dollar_tag == token) dollar_tag = ""
      rest = substr(rest, RSTART + RLENGTH)
    }
  }
  {
    trimmed = $0
    sub(/^[[:space:]]+/, "", trimmed)
    sub(/[[:space:]]+$/, "", trimmed)
    keyword = toupper(trimmed)
    if (dollar_tag == "" && (keyword == "BEGIN;" || keyword == "COMMIT;")) {
      update_dollar_state($0)
      next
    }
    print
    update_dollar_state($0)
  }
' "$down_file" > "$prepared"

cat >> "$RUNNER" <<SQL
SELECT EXISTS(
  SELECT 1 FROM platform_schema_migrations WHERE migration_name='$migration_name'
) AS migration_exists \\gset
\\if :migration_exists
  BEGIN;
  \\i '$prepared'
  DELETE FROM platform_schema_migrations WHERE migration_name='$migration_name';
  COMMIT;
  \\echo "Downgraded: $migration_name"
\\else
  \\echo "Migration is not applied: $migration_name"
  \\quit 4
\\endif
SELECT pg_advisory_unlock(731942018);
SQL

psql "$PG_DSN" -X -v ON_ERROR_STOP=1 -f "$RUNNER"
