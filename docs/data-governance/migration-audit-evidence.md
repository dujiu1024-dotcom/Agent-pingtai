# Migration audit evidence journal

```python
from agent_platform.data_governance.migration_audit import (
    append_migration_audit_event,
    list_migration_audit_events,
    verify_migration_audit_chain,
)

event = append_migration_audit_event(
    "var/audit/migration-audit.sqlite3",
    tenant_id="tenant-42",
    actor_id="operator-7",
    batch_id="legacy-import-2026-11",
    action="migration_failed",
    result="failed",
    attempt=2,
    failed_items=["users:42", "documents:19"],
    evidence_refs=["s3://migration-evidence/diff.json", "run:ci-123"],
    details={"error_code": "TRANSFORM_INVALID"},
)
assert verify_migration_audit_chain(
    "var/audit/migration-audit.sqlite3", tenant_id="tenant-42"
)["valid"]
```

## Recorded evidence

Each event records tenant and actor identity, migration batch, action, outcome,
attempt number, failed item references, evidence references, bounded JSON
metadata, UTC timestamp, previous event hash and current event hash.

Events are chained independently per tenant. Verification recomputes every
event hash and previous-hash link. Tenant-scoped reads require an explicit
tenant ID and never return another tenant's events.

## Immutability and operational boundary

SQLite triggers reject ordinary UPDATE and DELETE statements. This protects
against accidental mutation, not a privileged database owner who can alter the
file or drop triggers. The hash chain detects content changes unless an attacker
can also rewrite all later hashes. For compliance-grade retention, store the
database on access-controlled persistent storage and periodically export or
sign the chain head in an independently administered immutable/WORM audit
system.

This module is an append-only audit journal for migration tooling; it does not
automatically intercept migration operations. Callers must append lifecycle
events at operation boundaries and retain the returned event hash with their
migration evidence. Do not put credentials, prompts, raw personal data or
unredacted exception messages in `details`; use stable error codes and opaque
references instead.
