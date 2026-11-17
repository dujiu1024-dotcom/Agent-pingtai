# Issue #17 / A-17-03 delivery and runtime acceptance

## Decision

**Status: partially evidenced; not accepted as complete.** This PR now wires the existing PostgreSQL quota store/service, policy repository/service, authenticated quota reporting/admin routes, append-only audit adapter, and stale-reservation maintenance worker into the production ASGI composition. The latest main CI still cannot be evidenced as green, and real production/field checks remain outstanding; mocks are not treated as field evidence.

Inspection baseline: `main` at `7d2ff4aa3f90ec141f1df07e1923b44044a85b00`.

## Repository delivery inventory

### Runtime and persistence
- Quota API/runtime contract: `docs/quotas/administration-runtime-integration.md`.
- Hierarchical policy model/audit: `docs/quotas/hierarchical-policy-model.md`.
- Concurrency admission/fairness/leases: `docs/quotas/concurrency-admission.md`.
- Usage and cost reporting: `docs/quotas/usage-and-cost-reporting.md`.
- Shared PostgreSQL adapter: `agent_platform/quotas/postgres.py`; process-local store is explicitly test/development-only in `agent_platform/quotas/service.py`.
- Concurrency and cleanup worker: `agent_platform/quotas/concurrency.py`.
- Immutable pricing catalog: `agent_platform/quotas/pricing.py`, `agent_platform/quotas/pricing_store.py`.
- Tenant object storage meter: `agent_platform/quotas/storage_meter.py`.

### PostgreSQL migration lineage
The inspected tree contains these quota-related migrations; the numbered sequence includes compatibility/repair migrations. Use the repository migration orchestration rather than cherry-picking only the earliest schema:
- Base budget/reservation/usage: `012_tenant_quotas_cost.sql`, `012_tenant_quotas_usage.sql`, `016_tenant_quotas_cost.sql`.
- Null safety/split token budgets: `030_quota_reserve_null_safety.sql`, `033_quota_split_token_budgets.sql`, `036_quota_split_token_budgets.sql`.
- Queue/worker-pool scopes: `037_queue_worker_concurrency_quotas.sql`, `038_queue_worker_concurrency_quotas.sql`, `038_queue_worker_quota_scopes.sql`, `041_queue_worker_concurrency_quotas.sql`.
- Leases/root-run attribution: `039_quota_reservation_leases.sql`, `042_quota_reservation_leases.sql`, `042_root_run_quota_attribution.sql`, `060_quota_reservation_leases.sql`.
- Reservation groups/pricing: `043_quota_reservation_groups.sql`, `050_quota_reservation_groups.sql`, `044_model_pricing_catalog.sql`, `051_model_pricing_catalog.sql`.
- Context window/hierarchical policies: `044_quota_context_window_budget.sql`, `052_quota_context_window_budget.sql`, `071_hierarchical_quota_policies.sql`.

Migration entrypoints: `scripts/migrate_postgres.sh`, `scripts/check_migrations.sh`, and `docs/deployment/database-migration-orchestration.md`.

### Runtime configuration/deployment
- Optional runtime dependencies including `psycopg[binary,pool]` and `redis`: `requirements-platform.txt`.
- Local templates: `deploy/.env.example`, `deploy/compose.yaml`.
- Production secret-file and immutable-image templates: `deploy/.env.production.example`, `deploy/production.compose.yaml`.
- Production DB/Redis/origin validation: `agent_platform/production_config.py`.
- Quota budgets are persisted through APIs/database; current production env templates do not define an independent quota-specific env block.

## Production composition implemented in this PR
- `agent_platform/production.py` now constructs `PostgresQuotaStore` and `QuotaService` from the same production PostgreSQL connection factory used by the rest of the platform; no in-memory quota fallback is used.
- The production root also constructs `PostgresQuotaPolicyRepository` / `QuotaPolicyService`, and exposes both through `app.state` for trusted host integrations.
- Quota reporting and budget administration routers are mounted with explicit `quota.manage` authorization. Tenant budgets are pinned to the authenticated tenant; project/agent/run/model/queue/worker-pool scopes require resource-level authorization and fail closed.
- Budget mutations are adapted to the existing append-only `PostgresAuditWriter`; audit failure prevents the budget mutation.
- `QuotaReservationMaintenanceWorker` is injected into the API composition and uses the existing startup/shutdown lifecycle hooks.
- Regression contract: `tests/test_issue_17_a_17_03_production_quota_wiring.py`; Ruff include updated in `pyproject.toml`. The Run-worker quota factory has focused coverage in `tests/test_issue_17_a_17_03_run_worker_quota_factory.py`, registered in Ruff and Platform Release Gate; operator instructions are in `docs/quotas/production-run-worker-quota-admission.md`.

## Remaining blocking delivery gaps
1. **Run worker queue/worker-pool admission has a production composition entrypoint (incremental).** `agent_platform/quotas/production_worker.py` builds `RunExecutionWorker` with `QuotaConcurrencyLimiter` pinned to the shared `PostgresQuotaStore`; `agent_platform.production:create_production_app()` exposes this as `app.state.run_execution_worker_factory`. The worker acquires tenant queue and worker-pool slots before executor resolution. The deployment-owned host must still invoke the factory with trusted runtime callbacks and start the worker. This does not yet inject `QuotaEnforcedHarnessAdapter` around every model/provider call or prove runtime token/cost enforcement.
2. **Pricing/storage workers remain unconfigured.** No trusted provider price-feed credential/source or S3-compatible measurement endpoint is defined in production settings, so no background sync/measurement is started with fabricated or empty configuration.
3. **Alert/provider wiring remains an operator integration requirement.** The quota administration doc requires a durable warning callback backed by alert/outbox; deployment templates do not prove a live notification destination.
4. **No real external-system evidence is available.** Real S3-compatible tenant-prefix measurement, trusted price-feed ingestion, provider invoice reconciliation, multi-process admission/lease recovery and production migration/rollback have not been executed.

These are explicit production readiness gaps, not simulated passes.

## CI and test evidence
- Existing suites: `tests/test_issue_17_a_17_01_quota_contracts.py`, `tests/test_quota_postgres_integration.py`, `tests/test_quota_concurrency.py`, `tests/test_quota_pricing_catalog.py`, `tests/test_quota_storage_meter.py`, `tests/test_quota_usage_reporting_api.py`, `tests/test_quota_reservation_groups_postgres.py`.
- `.github/workflows/ci.yml` defines Python 3.11/3.12 unit/lint/type checks and PostgreSQL/Redis integration.
- Inspected main SHA: `7d2ff4aa3f90ec141f1df07e1923b44044a85b00`. Its check-runs contained only `prune` entries (queued/cancelled), not successful `ci`, security or platform release-gate evidence. The required latest-main-CI-green condition is not met by available GitHub evidence.
- The readiness test in this PR checks artifact presence and records the production composition gap; it does not substitute for the full CI pipeline or a real deployment.

## Production field evidence checklist (not run by instruction)

| Evidence | Required real evidence | Status |
|---|---|---|
| Production quota API | PostgreSQL-backed reporting/admin routes are now composed; deployed route and cross-tenant denial still require a live deployment | Composition implemented; field test not run |
| Multi-worker concurrency | Separate processes respect DB hard limit; crash/lease expiry restores capacity | Not run |
| Warning/alert delivery | Real alert/outbox destination receives warning and records acknowledgement/recovery | Not run |
| Pricing source | Trusted provider feed revision persisted immutably; outage/staleness observable | Not run |
| Storage measurement | Real S3-compatible endpoint lists tenant-prefixed objects and matches independently verified bytes | Not run |
| Billing reconciliation | Real provider invoice reconciles to ledger with traceable variance and duplicate protection | Not run |
| Migration/rollback | Production-like PostgreSQL backup, ordered migration, rollback/forward-fix and restore evidence | Not run |

No production server, external credentials, provider account or other external resource was requested or provisioned.

## Acceptance conclusion

Documentation, migration lineage, dependency declarations and deployment templates are present and cross-referenced. Production quota API/store/policy composition and stale-reservation worker lifecycle are now implemented in this PR, with static regression contracts. A-17-03 remains **pending/not accepted** until CI succeeds, runtime enforcement and configured pricing/storage/alert integrations are addressed, and required field evidence is collected or explicitly tracked as a follow-on field-acceptance item. Do not close Issue #17 on this evidence alone.
