# A-665-08 — SQLite → PostgreSQL migration consistency acceptance

## Scope

This change hardens the existing resumable migration batch diff used to verify
mapped SQLite source rows against the migration target. It is limited to
Issue #665 / A-665-08.

## Acceptance rule

A batch is reported as consistent only when all of the following hold:

- the migration batch state is `completed`;
- every mapped source primary key exists in the target;
- every mapped row has the same transformed field values;
- the target contains no extra primary keys in any mapped table.

The report includes per-table source/target row counts, missing target rows,
extra target rows, mismatched rows and matched rows. It reports aggregate
counts, a boolean `consistent`, and a `consistency_rate`. Row values are
never emitted. A paused or otherwise incomplete batch cannot pass, even if
the currently copied rows happen to match.

## Automated coverage

`tests/test_migration_batches.py` now covers:

- exact row match after migration;
- missing target rows and changed fields;
- extra target-only rows (the previous report did not detect these);
- incomplete/paused batches being rejected as consistent;
- source immutability, resumability, idempotency and mapping validation.

The Platform Release Gate runs this suite via
`python -m unittest tests.test_migration_batches`.

## Reproduction

Use a disposable, sanitized SQLite source and a disposable target database
created with the target schema. Run the existing migration runner with the
complete, reviewed mapping file, then request the diff:

```sh
python scripts/run_legacy_migration.py \
  --source /path/to/sanitized-legacy.sqlite \
  --target /path/to/migration-target.sqlite \
  --mapping /path/to/reviewed-mapping.json \
  --batch-id issue-665-a-665-08

python scripts/run_legacy_migration.py \
  --source /path/to/sanitized-legacy.sqlite \
  --target /path/to/migration-target.sqlite \
  --mapping /path/to/reviewed-mapping.json \
  --batch-id issue-665-a-665-08 \
  --action diff
```

The command's diff output is the acceptance evidence; require
`state=completed`, `consistent=true`, zero missing/extra/mismatched rows,
and equal source/target row totals for every mapped table.

## Evidence boundary / outstanding blocker

No sanitized legacy SQLite snapshot or corresponding PostgreSQL target
snapshot was attached to this task, and the existing batch runner currently
targets SQLite. Therefore this PR proves the comparator's acceptance
semantics with isolated test fixtures; it does **not** claim that a real
legacy-to-PostgreSQL migration has run or that production data is 100%
consistent. A complete sanitized source snapshot, PostgreSQL export and
reviewed table/column mapping are still required to make that factual claim.
No production data, real server, or external resource was requested or used;
real-device/production validation is skipped as instructed.
