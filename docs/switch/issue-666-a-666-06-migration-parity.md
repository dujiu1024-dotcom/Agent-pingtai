# A-666-06 SQLite → PostgreSQL migration parity acceptance

## Scope

This acceptance suite validates the existing read-only reconciliation command
`scripts/migrate_sqlite_to_pg.py`. It does not perform a database migration and
does not claim that a production dataset has been copied.

## Automated evidence

Run:

```bash
python -m unittest tests.test_issue_666_a_666_06_migration_parity -v
```

The suite creates isolated SQLite source data and independently exported
PostgreSQL-style JSONL snapshots across identity, work, and knowledge chains.
It verifies:

- exact row/value parity and aggregate checksums;
- zero missing, extra, or changed rows on a matching snapshot;
- explicit counts and failure status for missing, extra, and changed rows;
- rejection of duplicate target primary keys, missing snapshots, and sensitive
  column mappings;
- read-only behavior: no source mutation, no PostgreSQL connection, and no
  writes performed by the reconciliation command.

The test is included in Ruff's explicit include list and the Platform Release
Gate focused-unit job.

## Acceptance boundary

This is a deterministic contract test of the reconciliation/evidence tool.
It does not prove that the complete legacy `agency.db` was exported, that
production PostgreSQL contains every migrated row, or that a live migration
completed with 100% parity. A-666-06 must remain unaccepted until an operator
runs the command against immutable, checksummed production exports for every
in-scope table and attaches the full report, source/target snapshot identities,
row counts, checksums, and exception disposition to the cutover record.
