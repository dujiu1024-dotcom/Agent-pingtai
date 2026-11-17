# Migration and data lifecycle audit evidence

```python
from agent_platform.data_governance.lifecycle_audit import (
    LifecycleAuditService, PostgresLifecycleAuditRepository,
)

repository = PostgresLifecycleAuditRepository(connection_factory)
audit = LifecycleAuditService(repository)

# Write a successful batch event. Use identifiers and bounded scope only.
await audit.record(
    tenant_id="tenant-123",
    actor_id="operator-456",
    action="migration.batch.apply",
    outcome="succeeded",
    scope={"batch_id": "batch-2026-04", "source": "legacy", "destination": "platform"},
    correlation_id="a UUID shared by all attempts for this operation",
    attempt=1,
    evidence={"manifest_sha256": "the manifest digest", "rows_checked": 1200},
)
```

## Event contract

Each immutable event records the tenant, human/service actor, action, bounded resource or batch scope, outcome, correlation ID, attempt number, optional failure code, timestamp, and optional SHA-256 digest of compliance evidence. Raw source rows, export contents, credentials, prompts, and provider payloads must never be supplied as evidence.

Supported outcomes are `started`, `succeeded`, `failed`, `denied`, and `retry_scheduled`. Failed attempts require a stable failure code. A retry uses the same correlation ID and scope with an incremented attempt number. `record_failure_and_retry` writes the failure and retry scheduling events sequentially; callers should treat a writer failure as a hard audit failure and must not claim the operation is durably audited.

## Storage and access

Migration `071_data_lifecycle_audit.sql` creates a tenant-scoped append-only ledger, tenant/time and correlation indexes, and a database trigger rejecting UPDATE, DELETE, and TRUNCATE. `list_events` always filters by tenant and accepts an optional correlation ID; it enforces a bounded result limit. Production deployments must grant the application role INSERT/SELECT only and deny TRUNCATE/DDL; the trigger is defense in depth, not a substitute for least-privilege database roles.

Evidence is stored as a SHA-256 digest rather than raw content. This proves later byte-level consistency only when the original evidence is independently retained in an approved evidence store. It does not, by itself, establish legal admissibility, trusted timestamping, or production compliance certification.
