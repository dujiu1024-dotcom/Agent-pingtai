# A-72-03 Run budget and actual usage readback

The Run-scoped PostgreSQL ledger is the source of truth for budget limits,
reservations, provider-reported actual usage, estimate deltas and overrun reasons.

## Read APIs

- `GET /api/platform/v1/runs/{run_id}/budget-usage`
  - Requires the existing Run `read` authorization decision and a tenant-owned
    Run record.
  - The host supplies `resolve_run_budget_ledger` to `create_runs_router`.
- `GET /api/platform/v1/audit/run-budget-usage?run_id={run_id}`
  - Requires authenticated identity and the configured audit read permission.
  - The host supplies `run_budget_reader` to `create_audit_router`.

Both endpoints call `PostgresRunBudgetLedger.get_run_budget_report(tenant_id,
run_id)`; callers cannot choose a tenant. Missing budget accounts return 404.
The report contains the configured maximum, reserved and actual totals,
remaining budget, overrun reason, ordered per-model-call estimate/actual fields,
settlement deltas and counts for settled/reserved/rejected calls. Monetary
values are serialized as decimal strings to avoid floating-point loss.

Reserved and rejected calls remain visible even when no provider actual usage
exists; actual fields are null until settlement. Estimated totals are never
presented as provider actuals.

## Verification boundary

The focused API tests cover Run authorization, tenant scoping and the report
contract using a deterministic reader. They do not claim a live PostgreSQL
deployment or production audit-system verification; those remain deployment
evidence.
