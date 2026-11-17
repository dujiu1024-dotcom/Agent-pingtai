# Database migration orchestration

Database changes use an expand/contract sequence so old and new application
versions can coexist during rolling deployment.

1. **Plan**: declare source/target schema versions and reader/writer compatibility
   ranges for both the forward rollout and the old-version rollback path.
2. **Expand**: add nullable columns, additive tables/indexes, and compatibility
   views. Do not rename or drop fields used by the old application.
3. **Dual-write**: keep the old schema authoritative while writing the new
   representation. A shadow-write failure is recorded through a durable
   reconciliation callback; primary-write failures are surfaced immediately.
4. **Backfill**: run idempotent, resumable batches and track progress/checkpoints.
5. **Validate**: compare counts, key coverage and deterministic checksums. A
   validation digest is required before traffic can read the new schema.
6. **Cut over**: switch reads only after compatibility and validation gates pass.
   Keep dual-write and the old application/schema path available during the
   rollback window.
7. **Contract**: remove legacy columns/views only after the rollback window has
   explicitly closed. This is the point after which application rollback is
   no longer supported by the plan.

## Compatibility rules

The migration plan declares supported application reader and writer versions
against the expanded schema. Both the previous and new application versions
must be compatible before the plan can be created. The rollback compatibility
range is validated independently. Runtime checks can call
`verify_compatibility` before starting an application instance.

## Dual-write behavior

`DualWriteCoordinator` writes to the primary store first, then the shadow
store. If the primary fails, no shadow write is attempted. If the shadow fails,
the coordinator invokes a durable reconciliation enqueue callback and returns
the retry key; callers must monitor and drain that queue before validation or
cutover. The callback must persist payload identity and retry state outside the
process.

## Operational boundary

The orchestration state machine is provider-neutral and does not execute DDL,
manage database transactions, or deploy application traffic. Production
adapters must persist phase transitions, serialize concurrent operators,
make DDL/backfill steps idempotent, scope operations to tenant/environment,
and emit audit events. Never run destructive contract steps while old
application instances or rollback obligations remain.
