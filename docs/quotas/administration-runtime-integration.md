# Quota administration and runtime enforcement

The quota control plane exposes tenant-scoped policy reads and writes under
`/api/platform/v1/quotas`:

- `GET /budgets/{scope_type}/{scope_id}` reads the active policy.
- `PUT /budgets/{scope_type}/{scope_id}` validates and updates a policy.
- `GET /usage/current/{scope_type}/{scope_id}` returns current-period token,
  cost and concurrency counters.
- The existing `POST /budgets` endpoint remains compatible and is audited
  through the same configured audit writer.

Tenant scopes must match the authenticated principal. Project, Agent, Run,
model, queue and worker-pool scopes require an explicit scope authorizer.
Budget writes require the quota-management authorizer and an audit writer.
The audit event is written before persistence; if audit is unavailable or
fails, the policy mutation is rejected with 503.

At runtime, `QuotaEnforcedHarnessAdapter` continues to reserve hard quota
before provider dispatch. Optional `on_quota_warning` receives soft-limit
warnings before dispatch. If a configured warning callback fails, the adapter
releases the not-yet-dispatched reservation and fails closed. Hard-limit,
reservation, provider-usage and settlement failures remain fail-closed; unknown
provider outcomes retain reservations for reconciliation.

Production composition should pass `quota_audit_writer` (or the shared
`resource_audit_writer`) and provide a durable warning callback backed by the
platform alert/outbox pipeline. The in-memory quota store is for tests and
single-process development only; production requires the transactional shared
quota store.
