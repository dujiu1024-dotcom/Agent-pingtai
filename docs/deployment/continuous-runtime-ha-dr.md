# Issue #889 / L2-T12 — Continuous runtime, consistency and HA/DR drill

## Reuse boundary

This task composes existing #18 infrastructure rather than implementing duplicate persistence or recovery systems:

- `scripts/backup_platform.sh`, `backup_postgres.sh`, `backup_redis.sh`, `backup_object_storage.sh`: encrypted component backup artifacts, checksums and manifests.
- `scripts/restore_platform.sh` and component restore scripts: guarded restore order (PostgreSQL, Redis, objects); application writers remain stopped until operator validation.
- PostgreSQL Outbox and Redis Streams consumer receipts: durable event identity, bounded replay and consumer deduplication.
- Existing durable Run context snapshots, trace repository and Run replay service: restore context/trace and continue from persisted checkpoints.
- Existing task leases/idempotency facilities: recovery worker acquires a new lease after the old owner is fenced; external side effects must retain their idempotency key or be routed to human reconciliation.

No new queue, task store, Run store, backup format or provider-specific failover implementation is introduced here.

## Controlled drill plan

Print the task-specific plan:

```bash
python scripts/continuous_runtime_drill.py --plan
```

The plan is evidence orchestration only. It does not stop services, create backups, restore databases, promote replicas, replay messages, switch DNS or change traffic. Those actions remain under the existing #18 operator runbook and environment change controls.

## Cross-component consistency contract

1. Freeze API writers, schedulers, workers and queue consumers. Drain in-flight work and assign one `checkpoint_id` to the coordinated recovery point.
2. Run the existing encrypted backup scripts for deployment config, PostgreSQL, Redis and object storage. Preserve each artifact, SHA-256 sidecar, manifest, encryption identity reference and UTC creation time.
3. Restore in the existing guarded order: PostgreSQL, Redis, objects; restore configuration/secrets using the approved secret manager. Keep application writers stopped during restoration.
4. Verify database-to-object references, PostgreSQL outbox state, Redis consumer receipts, vector-index rebuild/reconciliation, audit continuity and tenant isolation. Redis cache may be rebuilt from authoritative PostgreSQL state.
5. Reconcile message replay by stable `event_id`; consumers must consult durable receipts. Replay is at-least-once delivery, not exactly-once transport.
6. Recover Run context and trace snapshots. A pending Run must either resume from a durable checkpoint with its original idempotency key or enter explicit human handoff. Never silently drop pending work or replay an external side effect without a durable idempotency/receipt check.
7. Fence the former primary before worker takeover or promotion. Validate a controlled canary, then fail back only through the approved runbook.

The artifacts are not an atomic distributed snapshot. The shared checkpoint ID and freeze interval are evidence that the operator coordinated the capture; they do not remove the need for post-restore reconciliation. The validator also enforces a maximum component snapshot timestamp skew of 24 hours. Deployments should use a tighter window where their recovery objectives require it.

## Evidence record

Create a JSON record outside the protected system and keep it with the drill artifacts. Record the exercised environment, topology, platform version and fault-injection scenario. The validator requires timezone-aware `last_known_good_write_at`, `incident_declared_at` and `traffic_resumed_at`; RPO is incident declaration minus last known good write, RTO is traffic resumed minus incident declaration.

It also requires:
- four component backups (config, PostgreSQL, Redis, objects), each with artifact name, 64-character SHA-256, manifest reference, UTC timestamp, shared `checkpoint_id` and successful restore verification;
- continuity checks for durable task checkpoints, worker lease takeover, stable Outbox event IDs, durable consumer receipts, Run context/trace recovery, pending Run disposition, PostgreSQL/Redis/object restore, vector index, audit continuity, tenant isolation, primary fencing and controlled failback;
- every pending Run listed as `resumed` or `human_handoff`, preserving its idempotency key; duplicate external effects must be exactly zero;
- evidence references and independent reviewer sign-off.

Validate the completed record:

```bash
python scripts/continuous_runtime_drill.py --validate-record /secure/drills/DR-YYYY-NNN.json
```

Exit codes: 0 = submitted evidence meets configured objectives and gates; 1 = evidence or objective failure; 2 = malformed input/configuration. A passing report validates supplied evidence only. It does not independently prove that the production drill happened or that HA/DR objectives are achieved.

## Recovery objectives and limitations

The validator uses the existing `deploy/disaster-recovery-policy.json` full-platform planning targets: RPO 24 hours and RTO 8 hours. These are targets, not guarantees or measured results. Only a real, independently reviewed, isolated-environment restore/failover exercise can establish measured RPO/RTO. CI unit tests validate the evidence gate; they do not run real PostgreSQL/Redis/object-store disaster recovery, multi-node takeover, network partition, or production failback. No real infrastructure is provisioned by this task.
