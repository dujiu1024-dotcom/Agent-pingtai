# Backup and disaster recovery policy

This module defines provider-neutral policy and orchestration primitives. A production
adapter supplies snapshot creation, durable catalog persistence, object-store lifecycle,
encryption/key management, and off-site replication.

## Policy

- Full snapshots are governed by `snapshot_interval` and `full_retention`.
- Incremental snapshots are governed by `incremental_interval` and
  `incremental_retention`; each incremental references its immediate parent.
- The catalog enforces tenant/resource isolation and complete chains terminating in a
  full snapshot. Restore must validate the chain before invoking a restore adapter.
- When `offsite_required` is enabled, the coordinator publishes a backup only after
  replication returns a durable off-site URI.
- `max_rpo` and `max_rto` express recovery objectives. `meets_rpo` evaluates the
  age of a recovery point; restore drills record measured duration and verification
  evidence, failing when the RTO is exceeded.

## Scheduling and operations

A scheduler should call `BackupCoordinator.run` at the configured full and incremental
intervals, with a full snapshot establishing each chain. The scheduler must persist
its last successful run and use a durable job/lease mechanism to prevent duplicate
concurrent executions. Call `BackupCatalog.prune` only after verifying retention and
legal-hold policy in the persistent catalog.

## Restore drills

Run drills at least every `restore_drill_interval` in an isolated recovery environment.
The restore adapter should replay the returned full-to-incremental chain, verify
checksums, schema, row counts and application-level invariants, then call
`RestoreDrill.finish` with a concise evidence summary. Persist drill state and
evidence outside the system being protected.

## Security and production boundary

Use encrypted storage and transport, separate off-site credentials/accounts, immutable
or object-locked retention where required, least-privilege restore identities, and
audited key rotation. This package does not execute cloud snapshots, schedule jobs,
perform database restores, or guarantee durability: those operations belong to
infrastructure-specific adapters and deployment configuration.
