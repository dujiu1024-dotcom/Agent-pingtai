# S-33-02 / S-666-02 — SQLite → PostgreSQL migration runner

## Scope and safety

The runner consumes the deterministic, read-only JSONL export produced by `scripts/export_legacy_sqlite.py`. It imports into **pre-created PostgreSQL tables** according to an explicit JSON mapping. It does not infer schema migrations, create tables, or silently transform data.

Default mode is dry-run. Dry-run validates the manifest, file hashes, row counts, mapping coverage, destination table/column existence and empty-target precondition inside a PostgreSQL `BEGIN READ ONLY` transaction. It issues no INSERT and always rolls back.

Apply requires explicit `--apply`. All mapped tables are inserted in one PostgreSQL transaction; any error rolls back the complete import. Destination tables must be empty, so this runner never overwrites existing production data. Run against an isolated staging database first and take a verified backup before any production use.

## Export and mapping

```sh
python scripts/export_legacy_sqlite.py /secure/agency.db /secure/export-2026-11-18
```

Create a reviewed mapping JSON (example only; adapt it to the approved schema):

```json
{
  "version": 1,
  "tables": [
    {
      "source": "legacy_users",
      "target_schema": "public",
      "target": "platform_users",
      "columns": {"id": "id", "email": "email", "created_at": "created_at"},
      "primary_key": ["id"]
    }
  ]
}
```

The mapping must cover every exported table exactly once. Source and target identifiers are restricted to simple SQL identifiers. Source columns must exist in the export manifest; mapped target columns must exist in PostgreSQL. Exported redacted values are rejected to prevent accidental lossy import. Binary JSONL wrappers are decoded to bytes.

## Dry-run (default)

```sh
AGENT_PLATFORM_MIGRATION_DSN='postgresql://…' \
  python scripts/migrate_sqlite_to_pg.py --export-dir /secure/export-2026-11-18 \
  --mapping /secure/mapping.json
```

Dry-run returns per-table row counts and `status=validated`; it does not mutate target data.

## Apply

After reviewing the export checksum manifest, mapping, destination schema and backup:

```sh
AGENT_PLATFORM_MIGRATION_DSN='postgresql://…' \
  python scripts/migrate_sqlite_to_pg.py --export-dir /secure/export-2026-11-18 \
  --mapping /secure/mapping.json --apply
```

Apply refuses any non-empty destination table, locks each target table against concurrent writes during the transaction, and commits once after all rows in all tables are inserted. Any failure rolls back the entire transaction. The runner deliberately does not auto-create schema or resolve conflicts.

## Evidence and limits

The JSONL export and manifest are checksummed; the runner reports per-table and total imported row counts. Follow with `scripts/export_legacy_sqlite.py` on an immutable PostgreSQL-derived staging export and `scripts/migration_diff.py` to compare canonical rows. This code does not provide an approved mapping for every historical table, PostgreSQL DDL/migrations for every legacy schema, or production credentials. No production database has been imported or compared in this change. Full migration acceptance still requires the complete source snapshot, approved mapping, target snapshot and zero-difference reconciliation evidence.
