# Issue #889 / L3-T16 — Control-plane degradation, policy sync and Run handoff

## Scope

This increment composes the existing offline authorization evaluator (#931), PostgreSQL Run lease/recovery implementation (#18) and continuity evidence gate (#932). It adds the missing orchestration boundary; it does not duplicate identity, policy, task persistence, worker leases, backup or Outbox implementations.

## Policy snapshot synchronization

`OfflinePolicySynchronizer` accepts a tenant-scoped signed snapshot only after the deployment-provided signature verifier approves it. It checks the validity interval, tenant, monotonic revision and monotonic revocation epoch, then delegates an atomic compare-and-swap to the durable snapshot store. Replaying the identical revision is idempotent; same-revision content changes and revision/revocation rollback are rejected. Audit failure propagates and the caller must not mark synchronization successful.

The store adapter must implement `PolicySnapshotStore.put_if_newer` as one database transaction/CAS. Signature verification must use the platform's trusted key registry; callers must not trust a signature flag supplied by an API request. Snapshot payloads contain policy metadata only, not plaintext secrets.

## Control-plane recovery ordering

`ControlPlaneReconciler` tracks ONLINE, DEGRADED and RECONCILING. Reconnect is strictly ordered:

1. Synchronize the newest verified policy snapshot.
2. Apply signed revocations before any replay.
3. Reconcile license state.
4. Replay durable Outbox events using existing stable event IDs and consumer receipts.
5. Resume paused Runs only after the preceding stages succeed.

Any failure, including a failed transition audit write, returns the mode to DEGRADED and stops the sequence. Recovery does not begin if its start audit cannot be persisted; a failed final ONLINE audit also leaves the reconciler DEGRADED. It never resumes Runs after a failed revocation or license reconciliation.

## Cross-node takeover and failback

`RunNodeHandoff` composes deployment fencing and the existing worker lifecycle. It stops the former worker, fences the former node, requires an affirmative fence confirmation, and only then starts the target worker. Failback uses the same sequence in reverse. If fencing cannot be confirmed, takeover is refused.

The existing PostgreSQL `PostgresRunStateStore` remains the authority for worker leases and `claim_workbench_tasks`; this module does not create a parallel lease table or directly transfer a Run. The deployment adapter must ensure that stopping/fencing a node prevents its process from renewing leases or producing side effects. The target worker then claims eligible Runs through the existing shared store, preserving checkpoint and idempotency behavior.

## Automated tests

`tests/test_hybrid_control_plane.py` covers policy revision and revocation monotonicity, tenant/signature rejection, idempotent sync, revocation-before-replay ordering, failed reconciliation, fenced failover/failback ordering and refusal to start an unfenced target.

CI runs this focused suite and Ruff independently on Python 3.11 and 3.12. This is software-level validation only.

## Explicit acceptance boundary

No real control-plane outage, cross-node process fencing, multi-node Run takeover, production failback, external system or real server was exercised. The deployer must provide trusted signature verification, durable atomic snapshot storage, node fencing and worker lifecycle adapters. Existing #932 drill evidence is still required for measured HA/DR results; CI does not establish RPO/RTO or production safety.
