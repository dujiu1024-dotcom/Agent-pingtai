# S-33-06 / S-666-06 — SQLite → PostgreSQL zero-difference parity evidence

## Purpose

This verifier supplies the repeatable automated comparison step for the S-33-06 requirement. It compares the checksummed, read-only SQLite export from S-33-02 with an independently exported PostgreSQL target snapshot. It does not connect to either database and never mutates data.

## Prepare snapshots

1. Produce a complete SQLite export with `scripts/export_legacy_sqlite.py`.
2. Use the reviewed S-33-02 mapping JSON. The mapping must cover every exported source table and column and declare the source primary key.
3. Export each mapped PostgreSQL target table independently as JSONL. Each row must be a JSON object whose keys are the mapped target column names. Use deterministic JSON encoding; binary values use the exporter's `{"$binary_base64":"..."}` representation.
4. Name each target file `<target_schema>__<target_table>.jsonl` (for example, `public__platform_users.jsonl`). The target directory must contain exactly one file for every mapped table and no untracked files.

## Run

```sh
python scripts/verify_sqlite_postgres_parity.py \
  --export-dir /secure/sqlite-export \
  --target-snapshot-dir /secure/postgres-export \
  --mapping /secure/approved-mapping.json
```

Exit status is 0 only when every mapped table has identical primary-key sets and identical row payloads. Exit status 1 means differences exist; exit status 2 means evidence is incomplete or malformed. The report contains per-table and aggregate row counts, missing/extra/changed counts, and deterministic SHA-256 digests; it does not print row contents.

## Automated coverage and production boundary

Focused tests cover zero-diff parity, order-independent checksums, missing/extra/changed records, duplicate keys, target-column drift, target table inventory drift, and source-manifest checksum drift. The tests are registered in Ruff and Platform Release Gate.

Passing these tests validates the verifier's behavior only. S-33-06 remains pending until the deployment owner supplies a complete approved source snapshot and mapping, executes the migration into an isolated staging PostgreSQL database, exports every target table, and attaches a zero-difference report plus backup/restore evidence. No production database was accessed by this change.
