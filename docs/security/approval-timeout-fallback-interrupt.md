# Approval timeout, safe downgrade and emergency interruption

## Behavior

Approval remains valid only for the exact tenant, requester, Run, action digest
and policy version already enforced by `ApprovalService.approved_action_hash`.
Expiry is never interpreted as implicit approval.

`ApprovalTimeoutCoordinator` provides the worker-facing timeout decision flow:

- `deny`: preserve fail-closed behavior and record a timeout denial.
- `downgrade`: permit only an explicitly configured fallback whose trusted risk
  level is strictly lower than the original action and below the human-approval
  threshold. The fallback is separately authorized before its executor is called.
- `interrupt`: require a bound Run ID and invoke the tenant-scoped Run interrupt
  callback. A missing Run ID, missing callback, or interruption failure is a
  fail-closed result.

All outcomes use the mandatory `PostgresAuditSink` contract. Audit write failures
propagate to the worker instead of being swallowed. The Postgres repository
expires a bounded batch under row locks (`SKIP LOCKED`) and records the
expiration event in the same transaction.

## Composition contract

The platform worker composes the coordinator with:
1. `PostgresApprovalTimeoutRepository(connection_factory)`;
2. a trusted rule resolver keyed by action/policy (never request-body supplied);
3. the platform `PostgresAuditSink`;
4. a fallback authorizer that consults the trusted tool registry and a fallback
   executor that re-enters the normal Gateway/policy path; and/or
5. a Run interrupter bound to the authenticated tenant's `RunLifecycleManager.cancel`.

The coordinator deliberately does not call tools directly or treat a fallback
as pre-approved. The caller must schedule `process(tenant_id=..., limit=...)`
from its existing worker cadence. Use idempotent fallback executors because a
worker can lose its lease after a downstream action completes but before it
records the outcome. Real-server and live Run interruption drills are skipped
by this change.
