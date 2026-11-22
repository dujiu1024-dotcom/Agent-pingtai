# S-665-04 SQLite snapshot export and migration consistency acceptance

## Automated scope

The read-only acceptance path uses the repository's supported export and diff tools:

1. Create a synthetic, redacted SQLite snapshot with three business chains: project delivery, commercial finance, and knowledge assets.
2. Export through `scripts/export_legacy_sqlite.py`; the source is opened read-only and the manifest records table schema, primary keys, row counts and SHA-256 digests.
3. Verify the export with `scripts/verify_legacy_export.py`.
4. Compare each normalized source JSONL table against an independently produced target JSONL snapshot with `scripts/migration_diff.py`. The test also injects changed and extra target rows and verifies the mismatch is detected.
5. Verify sensitive columns are redacted and the source SQLite bytes remain unchanged.

Run:

```bash
python -m unittest tests.test_issue_665_s_665_04_migration_acceptance -v
```

The fixture covers 3 business chains, 10 tables and 10 synthetic rows. It validates export integrity and row-level diff behavior; it is not a production data migration.

## Evidence boundary

- The target JSONL files in CI are generated from the same synthetic source fixture to test deterministic matching and mismatch detection. They are not a PostgreSQL export.
- No PostgreSQL connection or write is performed by this dry-run parity suite. The production importer `scripts/migrate_sqlite_to_pg.py` is a separate explicit operation requiring an operator-supplied DSN and reviewed mapping.
- Real, redacted SQLite snapshots must be compared with independently exported PostgreSQL target snapshots before claiming 100% migration parity. No production data, server, credentials or external resources are requested or used.
