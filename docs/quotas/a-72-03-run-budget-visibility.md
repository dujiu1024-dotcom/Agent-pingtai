# Issue #72 / A-72-03 — Run budget and usage visibility

## Scope

This acceptance increment verifies the existing Run detail and audit-query contracts for budget and model-call usage. It does not change quota accounting or claim production database acceptance.

## API contract under test

- `GET /api/platform/v1/runs/{run_id}` returns a tenant-authorized Run and, when a budget ledger resolver is configured, its budget snapshot:
  - configured token/cost limits;
  - reserved and actual token/cost values;
  - remaining budget and overrun reason;
  - per-model-call estimated and actual token/cost usage and deltas.
- `GET /api/platform/v1/audit/run-budget-events?run_id=...` returns the same tenant-scoped model-call usage ledger and requires audit-read authorization.
- Run authorization is checked before the budget ledger is queried. The audit endpoint uses the authenticated Principal tenant rather than a caller-supplied tenant identifier.

## Automated coverage

`tests/test_issue_72_a_72_03_run_budget_visibility.py` covers:
- Run detail exposes the budget snapshot and estimated/actual usage values.
- Cross-tenant Run reads are denied before any budget-ledger query.
- Budget audit output contains estimate, actual and variance fields and passes the authenticated tenant to the reader.
- Audit permission denial prevents the budget usage query.

The test is registered in the Ruff include list and Platform Release Gate focused-unit job.

## Validation boundary

The focused tests use deterministic in-memory Run state and a ledger test double. They verify the HTTP composition contract, not PostgreSQL persistence, actual provider billing reconciliation, production authorization configuration or deployment wiring. Those require the relevant integration CI and production evidence. Do not mark A-72-03 fully accepted solely because this PR is open or its unit tests pass.
