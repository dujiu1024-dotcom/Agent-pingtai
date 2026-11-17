# Run context snapshots

`RunContextSnapshotService` assembles the execution context independently of model token budgeting (Issue #72). The in-memory store is intended for unit tests; multi-worker deployments should use `PostgresRunContextSnapshotStore`.

## Durable record

Migration `060_run_context_snapshots.sql` creates an append-only, tenant/Run-scoped table. Each revision stores the complete JSON context, bounded reference window, immutable snapshot UUID and SHA-256 digest. A composite foreign key ties snapshots to an existing Run and cascades when the Run is deleted.

The PostgreSQL adapter serializes revision allocation with a transaction-scoped advisory lock per tenant/Run, verifies the next revision, and inserts without update semantics. Reads select the latest revision. Digest validation occurs before persistence and again during `RunContextSnapshotService.rebuild`.

## Recovery contract

1. Resolve the Run under its tenant.
2. Call `rebuild(tenant_id, run_id)` before resuming the graph.
3. Missing snapshots, tenant mismatches, malformed UUIDs or digest mismatches fail closed; recovery must not silently reconstruct from a newer request or registry version.
4. The returned context and references are defensive JSON copies. Graph nodes cannot mutate the stored snapshot through the returned object.

Pinned references remain in the bounded reference window; duplicate reference IDs keep their latest metadata while preserving an earlier pin. Token counting and model-window truncation remain the responsibility of Issue #72.

## Validation boundary

Unit tests exercise PostgreSQL adapter transaction/query behavior with a deterministic fake connection. A real PostgreSQL migration and multi-process recovery drill remain deployment acceptance work; tests with a fake connection do not claim those have occurred.
