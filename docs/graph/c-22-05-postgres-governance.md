# C-22-05 PostgreSQL graph governance persistence

This change adds a durable adapter for the existing `GraphGovernanceService`.
The in-memory adapter remains the reference/test adapter; production hosts can
inject `PostgresGraphGovernanceRepository(tenant_id, connection_factory)`.

## Durable contracts

- Entity and relation snapshots have monotonically increasing versions; history rows are append-only.
- Mutations use tenant-scoped advisory transaction locks and compare-and-swap expected versions.
- Archive/expire/delete/correction/merge writes the new snapshot, history, audit event and invalidation outbox in the same transaction.
- Merge locks both entity IDs in deterministic order, rewires relations, removes self-loops and duplicate edges, and preserves relation history.
- Expiry sweep uses `FOR UPDATE SKIP LOCKED` so concurrent workers do not claim the same due entities.
- Outbox claiming uses leases, bounded attempts and worker ownership checks. Handlers must remain idempotent.
- All repository operations are bound to the constructor tenant; a mismatched tenant cannot read or mutate records.

## Host integration boundary

Apply migration `095_graph_governance_persistence.sql`, then project canonical graph entities and relations into this governance store via `add_entity` / `add_relation`. The adapter does not replace the existing canonical graph repository or claim that its records are automatically synchronized. Hosts must wire graph ingestion and invalidation handlers explicitly.

## Verification

Run `python -m unittest tests.test_graph_governance_postgres -v` and the existing
`tests.test_graph_governance` suite. Production PostgreSQL migration, concurrent
worker behavior and external index/cache invalidation remain deployment checks.
