# A-666-06 — Full migration scope coverage gate

## Purpose

A row-level diff over a hand-picked set of mappings cannot establish that the
whole legacy SQLite database was migrated. An omitted source table would never
appear in that diff. This change adds a fail-closed inventory check to the
existing migration batch toolkit.

## API

`validate_migration_scope(source_path, target_path, mappings, excluded_source_tables=...)`
opens both SQLite files read-only and:

- inventories every non-internal source table;
- rejects unknown source mappings and missing target tables;
- rejects duplicate source or target mappings;
- rejects source tables that are neither mapped nor explicitly excluded;
- requires a non-empty rationale for each explicit exclusion;
- reports whether all source tables are accounted for and whether the scope is
  eligible for a 100% consistency claim.

Any explicit exclusion makes
`eligible_for_100_percent_consistency=false`. This is deliberate: documented
exclusions may define a partial migration scope, but cannot be presented as
100% migration of the source database.

## Focused tests

```bash
python -m unittest tests.test_issue_666_a_666_06_migration_scope -v
```

The tests cover complete inventory, omitted tables, exclusion rationale,
unknown source/missing target tables, and duplicate mappings.

## Evidence boundary

This is a completeness guard for the existing SQLite staging migrator; it does
not itself prove the historical production migration. At the time of this
delivery, no complete, approved source `agency.db` snapshot and matching
PostgreSQL target export were supplied to the repository. The current batch
engine is SQLite-to-SQLite, not a PostgreSQL target adapter. Therefore A-666-06
must remain blocked until the real source snapshot, target PostgreSQL export,
approved table/column mapping, and full-data comparison report are supplied.
CI fixtures validate the guard's behavior only; they are not production
migration evidence.
