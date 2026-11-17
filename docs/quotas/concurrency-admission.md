# Concurrency, rate admission and tenant fairness

The quota reservation store is the hard concurrency authority. Reservations are
created atomically against the configured tenant/scope budget; duplicate active
Run/scope reservations are rejected. Queue and worker-pool budgets are
concurrency-only scopes.

## Admission policy

Use `TenantFairQuotaAdmissionController` in front of
`QuotaConcurrencyLimiter`:

- `strategy="reject"` returns `BudgetExceeded` immediately if capacity is full
  or another tenant currently owns the next queue turn.
- `strategy="queue"` waits for capacity, optionally bounded by
  `wait_timeout_seconds`. Timeout raises `BudgetExceeded`.
- Requests are FIFO within a tenant. Tenants sharing a scope are served in
  round-robin turns, so a saturated tenant cannot block another tenant's
  independently scoped capacity.
- Cancellation and timeout remove the waiter from the queue. The `slot()`
  context manager renews its reservation lease and releases it on exit.

Example:

```python
from agent_platform.quotas import AdmissionRequest, TenantFairQuotaAdmissionController

async with admission.slot(AdmissionRequest(
    tenant_id=tenant_id,
    run_id=run_id,
    scope_type="queue",
    scope_id="interactive",
    strategy="queue",
    wait_timeout_seconds=30,
)):
    await execute_run()
```

The fair queue is process-local to a controller instance. PostgreSQL-backed
reservation operations still enforce hard limits across workers, but deployments
that need globally ordered fairness across multiple application processes must
route a quota scope through one shared admission arbiter (or add a distributed
queue backend); local FIFO ordering is not global.

## Leases and orphan recovery

Use `QuotaConcurrencyLimiter.slot()` to heartbeat active reservations. The
existing PostgreSQL lease migration prevents expired leases from being renewed.
Run `QuotaReservationMaintenanceWorker` as a lifecycle-managed background
worker to reclaim expired reservations in bounded batches; the database uses
`FOR UPDATE SKIP LOCKED` so multiple cleanup workers do not reclaim the same
reservation. After an external cleanup cycle, call
`notify_capacity_changed(scope_type, scope_id)` for affected scopes to wake
queued callers.

## Rate limits

This task governs concurrency admission and queue pressure, not requests-per-
second token buckets. Rate limiting should be layered separately so a burst
window does not consume or replace a concurrency reservation.
