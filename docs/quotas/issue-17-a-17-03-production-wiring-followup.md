# Issue #17 / A-17-03 production quota composition follow-up

This change closes one repository-level production wiring gap identified in
[PR #675](https://github.com/dujiu1024-dotcom/Agent-pingtai/pull/675). It does
not mark A-17-03 accepted.

## Implemented

- `agent_platform.production:create_production_app()` now constructs
  `PostgresQuotaStore` from the same PostgreSQL connection factory used by
  the production services. It does not use the process-local in-memory store.
- A `QuotaService` is constructed over that shared store and injected into
  the quota reporting/administration API composition.
- `QuotaReservationMaintenanceWorker` is registered through the existing
  API startup/shutdown lifecycle. Its bounded stale-reservation cleanup is
  therefore managed by each production API process.
- Readiness checks verify both PostgreSQL connectivity and the presence of
  `public.platform_quota_budgets`; a missing quota migration cannot be
  reported as a ready production API.
- Quota management and project/agent scope authorization are delegated to
  the identity service with the `quota.manage` permission. Unsupported
  scopes fail closed.

## Automated evidence

`tests/test_production_quota_wiring.py` checks the production composition
contract: shared PostgreSQL store construction, service/worker construction,
API injection, permission gate and schema readiness guard.

The test is a static wiring regression check. It is not evidence that a
PostgreSQL server, multiple worker processes, external pricing provider,
object-storage endpoint or notification destination was exercised.

## Remaining acceptance evidence

A-17-03 remains open until current-main CI is green and operators provide
real deployment evidence for:

- multi-process reservation limits and crash/lease recovery;
- durable warning/alert delivery and acknowledgement;
- trusted model-price feed ingestion and stale-feed behavior;
- S3-compatible tenant-prefix storage measurement;
- provider invoice reconciliation against actual billing records;
- production migration and rollback.

No mock result is substituted for these field checks. The API quota audit
writer/alert destination and pricing/storage schedulers still require
environment-specific production integration. PR #675 remains the companion
delivery-inventory record; this follow-up is intentionally limited to the
production composition and cleanup lifecycle.
