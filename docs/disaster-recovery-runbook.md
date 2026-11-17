# Platform Disaster Recovery Runbook

This runbook defines a controlled recovery procedure for PostgreSQL, Redis, MinIO
objects, deployment configuration and application services. It complements
`deploy/BACKUP_POLICY.md` / `deploy/backup-policy.json` from the backup-policy
work item when that change is available on the deployment branch. Backup cadence
is not proof of recoverability: operators must verify the selected artifact,
checksum, manifest, encryption identity and restore compatibility before use.

## Recovery objectives

The following are **planning targets**, not measured guarantees or an SLA. They
must be validated by timed drills and revised using observed results. RPO is the
maximum acceptable data-loss window; RTO is the target time to restore service.

| Failure domain | Target RPO | Target RTO | Recovery source / caveat |
| --- | ---: | ---: | --- |
| PostgreSQL primary loss | 1 hour | 4 hours | Latest verified encrypted custom-format dump; actual RPO is the dump timestamp |
| Redis loss | 6 hours | 1 hour | Latest verified RDB snapshot; ephemeral/cache data may be rebuilt |
| MinIO bucket loss | 24 hours | 4 hours | Latest verified mirror archive; current object state only, not historical object versions |
| Deployment configuration | 24 hours | 2 hours | Encrypted config archive plus separately managed Secret/version references |
| Full platform/site loss | 24 hours | 8 hours | Slowest required component governs service recovery; external DNS/identity dependencies add time |

Targets assume backup artifacts and decryption identities are available in a
separate failure domain, compatible compute/storage capacity exists, and operators
are available. The effective platform RPO is the oldest required component
snapshot that is mutually consistent; the effective RTO is measured from incident
declaration until validated traffic is restored. Do not report these targets as
achieved until a drill records actual timestamps.

## Recovery order

1. **Declare and contain.** Incident commander records the incident, selects the
   recovery point, declares the recovery site, freezes deployments and prevents
   application writers/consumers from mutating state. Preserve logs and evidence.
2. **Provision dependencies.** Restore network, DNS, TLS, storage volumes,
   PostgreSQL/Redis/MinIO services and identity access at the recovery site.
   Confirm capacity and version compatibility. This is an operator/platform task.
3. **Restore configuration and secrets.** Restore the encrypted configuration
   archive to a restricted staging directory; review paths and ownership before
   applying. Rebind secret references from the approved secret manager. Never
   put plaintext secrets in the backup directory, shell history or drill record.
4. **Restore PostgreSQL.** Verify the selected `.dump.age`, sidecar checksum,
   manifest digest/database identity and AGE identity; run
   `scripts/restore_postgres.sh` with the explicit confirmation token. Resolve
   migration/version compatibility before proceeding.
5. **Restore Redis.** Keep Redis stopped while replacing its RDB. Verify the
   RDB, ownership and mode; run `scripts/restore_redis.sh` with both restore
   confirmation and stopped-service confirmation. Restart only after validation.
6. **Restore object data.** Verify bucket identity and archive contents; run
   `scripts/restore_object_storage.sh`. Default behavior mirrors without deleting
   destination-only objects. The destructive remove option requires a separate
   explicit review and is not part of the default recovery path.
7. **Validate application state.** Check schema/migration version, critical
   database invariants, Redis connectivity/keyspace expectations, object counts
   and sampled object hashes, secret resolution, health/readiness endpoints,
   queues/outbox lag and tenant-scoped smoke tests.
8. **Resume traffic gradually.** Start workers before accepting writes only where
   their dependencies and idempotency guarantees are verified. Route a small
   canary first, monitor errors/latency/queue lag, then restore normal traffic.
9. **Close and reconcile.** Record actual RPO/RTO, data gaps, manual repairs,
   follow-up actions and evidence. Keep the incident open until reconciliation
   and stakeholder sign-off are complete.

The helper `scripts/restore_platform.sh` invokes the existing PostgreSQL,
Redis and object restore scripts in that order. It requires a reviewed
`RESTORE-READY` marker and explicit confirmation. It does not stop services,
restore deployment configuration, create infrastructure, promote replicas,
switch DNS, or validate application semantics. Those remain deliberate operator
gates. Run each component script separately when partial recovery is required.

## Cross-component consistency

These backups are independent snapshots and are **not an atomic distributed
snapshot**. PostgreSQL, Redis and MinIO may represent different instants. A
successful checksum proves artifact integrity, not cross-system consistency.

- Before planned recovery, stop API write traffic, scheduled jobs, queue
  consumers and background workers; drain in-flight operations and record the
  freeze timestamp.
- Prefer a coordinated backup window: record each manifest timestamp and the
  database transaction/LSN watermark where available. Record Redis snapshot
  time and object inventory/versioning status.
- During unplanned recovery, choose the newest verified set that does not
  violate application invariants. Treat the oldest required snapshot as the
  conservative platform recovery point.
- After restore, reconcile database references against object keys; identify
  orphaned objects and missing objects. Reconcile outbox/event state and avoid
  replaying non-idempotent external side effects without receipts.
- Redis is treated as rebuildable cache/ephemeral state unless the application
  explicitly stores authoritative state there. Do not silently promote a stale
  cache snapshot over PostgreSQL authority.
- MinIO mirror backups do not retain prior object versions. Object versioning
  or immutable retention must be configured separately if historical recovery
  is required.

If the application cannot tolerate the snapshot skew, keep traffic stopped and
perform a domain-specific reconciliation or select an earlier coordinated
recovery point. Never claim zero data loss from these component backups.

## Failover and failback

Failover is a controlled decision, not an automatic consequence of a failed
health check.

1. Incident commander confirms primary-site impairment and obtains approval
   under the service's change/incident policy.
2. Fence the old primary (network isolation, storage fencing or provider-level
   lease) before promoting any replica. A split-brain primary must not accept
   writes.
3. Verify replication lag and recovery point; record expected data loss against
   RPO. If no trustworthy replica exists, use the backup recovery sequence.
4. Promote the designated PostgreSQL/Redis/MinIO endpoints using the
   infrastructure-specific procedure. Update secrets/configuration and DNS or
   load-balancer routing only after health checks pass.
5. Run read-only checks, then controlled write canary and idempotency/outbox
   checks. Expand traffic only after approval.
6. For failback, freeze writes, reconcile data in both directions using an
   explicitly chosen authority, verify replication, fence the recovery primary,
   switch routing, then resume traffic. Never reverse these steps ad hoc.

This repository does not encode provider-specific replica promotion or DNS
failover because topology, fencing and credentials are environment-specific.
Document those commands in the deployment's secured operations system, not here.

## Drill cadence and evidence

Run a tabletop review quarterly and a timed restore drill at least twice yearly,
plus after material changes to backup format, encryption, schema or topology.
Use an isolated recovery environment; this work item does not authorize live
production drills. Record evidence with
[`docs/disaster-recovery-drill-template.md`](disaster-recovery-drill-template.md).

A drill passes only when the selected artifacts decrypt and validate, all
required components restore, application invariants pass, measured RPO/RTO are
within approved objectives, and an independent reviewer signs off. A mocked
restore or shell syntax check is not a recovery drill.

## Stop conditions

Stop and escalate if checksum/manifest validation fails, AGE identity is
unavailable, artifact/target identity differs, PostgreSQL migration compatibility
is unknown, Redis cannot be confirmed stopped, object archive paths are unsafe,
the former primary cannot be fenced, or cross-component reconciliation detects
unexplained loss. Do not bypass confirmation tokens to meet an RTO target.


## Executable recovery plan and drill evidence gate

The approved objectives and mandatory gates are machine-readable in
`deploy/disaster-recovery-policy.json`. Print the current recovery plan:

```sh
python scripts/disaster_recovery.py --plan
```

After an isolated exercise, copy
`docs/disaster-recovery-drill-template.md` into a JSON record using the field
names exercised in `tests/test_disaster_recovery_evidence.py`, then validate it:

```sh
python scripts/disaster_recovery.py --validate-record /secure/drills/DR-YYYY-NNN.json
```

The validator requires timezone-aware incident, last-known-good-write and
traffic-resumed timestamps. It computes measured RPO from the last known good
write to incident declaration and measured RTO from incident declaration to
traffic resumption. Full-platform records must include successful checks for
configuration, PostgreSQL, Redis and objects; database/object, outbox/event and
tenant authorization reconciliation; primary fencing and routing validation;
independent reviewer sign-off; and evidence references. Exit status is 0 for
a passing record, 1 for evidence/objective failures, and 2 for malformed input
or policy errors. Component-only scopes can declare their in-scope components.

The release-gate suite also runs an isolated PostgreSQL component drill
(`tests.test_issue_18_a_18_02_backup_restore_drill`). It creates temporary source
and target databases, generates an ephemeral AGE identity, runs the repository
encrypted backup and restore scripts, verifies a committed marker after restore,
and records timestamp-derived RPO/RTO with millisecond precision as a CI artifact.
The automated record leaves independent reviewer sign-off pending. This drill
does not exercise Redis, MinIO, configuration recovery, full-platform consistency,
replica promotion or DNS failover; it is not a full-platform or production drill.

A passing result evaluates the submitted record; it is not a live probe and
does not prove that a production restore, replica promotion or DNS failover
actually occurred. Keep raw logs, manifests and screenshots in the restricted
evidence system and put references—not secrets or customer data—in the record.
