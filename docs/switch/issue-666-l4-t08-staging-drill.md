# Issue #666 / L4-T08 — staging import, zero-diff reconciliation and rollback drill

## What CI executes

`scripts/issue_666_l4_t08_staging_drill.py` runs against the disposable PostgreSQL service already provisioned by `platform-release-gate`. It creates a synthetic SQLite legacy snapshot with identity, task and document rows (including binary payloads), exports it using the existing read-only exporter, validates the reviewed mapping, performs the existing importer dry-run and atomic apply, and compares canonical rows plus SHA-256 digests table by table.

The drill then models a post-cutover write in PostgreSQL and executes the existing `scripts/rollback_cutover.py` orchestrator with six real subprocess steps:

1. freeze writes;
2. reverse-sync PostgreSQL rows into a fresh SQLite legacy database;
3. verify reverse-sync parity;
4. switch the test gateway marker to legacy;
5. verify the legacy database health;
6. unfreeze legacy writes.

It fails closed on any mismatch, failed step, missing artifact, or incomplete rollback. The report contains table row counts, checksums, step durations and acceptance booleans. CI uploads `issue-666-l4-t08-staging-drill.json` as a 30-day artifact.

## Run locally against a disposable PostgreSQL database

```sh
python -m pip install -r requirements-platform.txt
python scripts/issue_666_l4_t08_staging_drill.py \
  --dsn "$AGENT_PLATFORM_TEST_DSN" \
  --evidence issue-666-l4-t08-staging-drill.json
```

Use only a disposable database: the script drops/recreates the three fixture tables in `public`. Never point it at production.

## Evidence boundary

This is a full executable integration drill against temporary CI infrastructure and synthetic data. It is not evidence that the approved full `agency.db` snapshot has been imported, that all production table mappings are complete, or that the deployed production gateway has switched. Those remain blocked on approved staging snapshot/schema/mapping and deployment-side gateway wiring. The CI result must not be represented as a production cutover or production five-minute rollback acceptance.
