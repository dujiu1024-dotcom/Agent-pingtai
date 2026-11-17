# Platform operations handbook

This handbook is the operator entry point for deployment, release upgrades, backup,
restore, rollback, validation and incident triage. It uses the production Compose
overlay and the existing component scripts. Commands that mutate production require
a reviewed change/incident approval and an exact confirmation token. Run from the
repository root unless paths are absolute.

## 1. Operating model and prerequisites

The reference deployment is a single-host Docker Compose topology: API, PostgreSQL,
Redis and MinIO. It is not a multi-host HA scheduler. DNS, TLS termination, external
secrets, firewall, monitoring delivery, managed replicas and off-site storage are
environment-owned integrations.

Required operator tools: Docker Engine with Compose v2, Python 3.11+, age, sha256sum,
and the native database/object clients required by the selected operation. Configure
the deployment-owned ASGI bootstrap as described in
[production environment](deployment/production-environment.md). The readiness endpoint
must check PostgreSQL, Redis and object storage, not only process liveness.

1. Copy deploy/.env.production.example to deploy/.env.production and replace all
   example values, including the immutable image digest and deployment-owned ASGI app.
2. Provision secret files through the approved secret manager. Do not store secret
   values in the env file, shell history, tickets or drill records.
3. Set the env file mode to 0600 and secret directories/files to restrictive ownership
   and permissions. Ensure backup age identity is held offline and recipient is known.
4. Confirm the host has capacity, durable volumes, time synchronization, monitoring,
   outbound registry access and a tested recovery destination.

## 2. Unified operator command

scripts/platform_ops.sh is the supported command entry point. It serializes operations
with a host lock and defaults to the production Compose overlay.

Read-only validation:

```sh
scripts/platform_ops.sh preflight
scripts/platform_ops.sh status
scripts/platform_ops.sh health
scripts/platform_ops.sh recovery-plan
scripts/platform_ops.sh verify-backups
RELEASE_MANIFEST=/secure/releases/release.json scripts/platform_ops.sh upgrade-plan
```

Production-changing commands require explicit second-argument confirmation:

```sh
scripts/platform_ops.sh deploy CONFIRM-PRODUCTION-DEPLOY
AGE_RECIPIENT='age1...' scripts/platform_ops.sh backup CONFIRM-PLATFORM-BACKUP
scripts/platform_ops.sh restore CONFIRM-PLATFORM-RESTORE
ROLLBACK_SNAPSHOT=/secure/path/env-YYYYMMDDTHHMMSSZ scripts/platform_ops.sh rollback CONFIRM-PRODUCTION-ROLLBACK
```

Review command output and change approval before supplying a confirmation token.
Do not put secrets in command-line arguments. The lock is host-local and does not
replace a distributed deployment lock for multi-host environments.

## 3. Deploy

1. Review the release commit, immutable image digest, config revision and change window.
2. Confirm the previous release snapshot and verified backup set exist.
3. Run preflight; it checks the env file, Compose interpolation and required tools.
4. Execute the confirmed deploy command. The script snapshots the current env file,
   validates Compose, pulls the digest-pinned image, waits for healthy services and
   probes API readiness. It writes a success marker only after readiness passes.
5. Confirm all services with status, readiness and external synthetic checks. Route
   traffic only after the load balancer reports healthy targets.

The command does not run database migrations. Migrations are a separately approved
release step and must follow the expand/contract compatibility contract.

## 4. Upgrade

Prepare a release manifest using the schema and examples in
docs/deployment/release-upgrade.md. Run:

```sh
RELEASE_MANIFEST=/secure/releases/release.json scripts/platform_ops.sh upgrade-plan
```

The planner validates semantic versions, immutable image digests, old/new compatibility,
migration preflight, backup reference, rollout batches and rollback references. It emits
ordered migration/rollout/rollback plans; it does not execute them. The deployment
executor must pause between batches, evaluate readiness/error/latency/queue lag gates,
and stop on any failed gate. Destructive contract migrations wait until the compatibility
window closes. Record operator, timestamps, evidence and final commit.

## 5. Backup and integrity verification

The policy in deploy/backup-policy.json defines the component schedule and retention.
A manual backup run requires AGE_RECIPIENT and writes encrypted artifacts, SHA-256
sidecars and manifests. The unified command runs verification immediately afterward.
For stronger verification, run decryption and native-format checks in a restricted
environment with the offline identity available:

```sh
AGE_IDENTITY=/secure/offline/age-identity.txt python3 scripts/verify_platform_backups.py --backup-dir backups --decrypt
```

A checksum validates ciphertext integrity, not cross-component consistency. Confirm
artifact freshness, manifest component/database/bucket identity, encryption identity
availability and off-host replication. Investigate every failed or missing artifact
before any restore. Retention deletion is irreversible; confirm policy and off-host
copies before changing retention.

## 6. Restore and disaster recovery

Use docs/disaster-recovery-runbook.md as the authority for incident command, RPO/RTO,
recovery sequence, cross-component reconciliation, failover fencing and failback.
Restore is destructive and requires a recovery window, incident approval, writers and
consumers stopped, target identity confirmed, and a selected verified backup set.

The existing restore scripts enforce separate confirmation variables and checks:
PostgreSQL checksum/manifest/database identity; Redis stopped-service confirmation and
RDB validation; object bucket identity and safe archive paths. Configure all required
variables from the approved secret manager, create BACKUP_DIR/RESTORE-READY only after
the preconditions are met, and invoke the restore command with its explicit token.
The sequence is PostgreSQL, Redis, then objects. Keep application writers stopped until
schema, tenant isolation, object references, outbox/event state, Redis and health checks
pass. The helper does not provision infrastructure, stop services, promote replicas,
switch DNS or prove application semantics.

For site failure, fence the former primary before promotion to prevent split brain.
Record the selected recovery point and expected data loss. Promote provider-managed
replicas and change routing only through the environment-specific approved procedure.
The repository does not contain provider credentials or universal replica-promotion
commands.

## 7. Rollback

For an application release rollback, stop the rollout, drain new-version traffic, restore
the previous immutable image and configuration revision, then apply the approved
compensating migration or follow the documented backup-restore path. Never assume a
database downgrade is safe. Use the generated release rollback plan and record evidence.

The unified infrastructure rollback command requires a reviewed env snapshot under
deploy/releases. It validates the snapshot, saves the current configuration, applies the
snapshot to the production Compose overlay and waits for readiness. If rollback fails,
it restores the previous env file and exits non-zero; operators must then recover service
health manually. This changes configuration/image references; it does not reverse
database schema changes or restore data.

## 8. Validation checklist

- [ ] Compose config renders with no unresolved variables or secrets committed.
- [ ] All expected services are healthy; API readiness checks every durable dependency.
- [ ] Database migration version and critical invariants match the release.
- [ ] Redis connectivity, stream/consumer lag and cache rebuild behavior are acceptable.
- [ ] MinIO bucket access, object inventory and sampled hashes are correct.
- [ ] Tenant-scoped authorization and representative end-to-end workflows pass.
- [ ] Queue/outbox lag, error rate, latency, resource saturation and alert delivery are normal.
- [ ] Backup artifacts, sidecars, manifests and decryption test pass.
- [ ] Release/restore/rollback evidence is stored in the restricted operations system.

## 9. Fault triage

| Symptom | First checks | Safe response |
| --- | --- | --- |
| Compose config fails | env file path/mode, required variables, YAML and image digest | Do not deploy; fix config and rerun preflight |
| API not ready | API logs, PostgreSQL/Redis/MinIO health, bootstrap dependency checks | Keep traffic out; restore dependencies before API |
| PostgreSQL unavailable | volume capacity, pg_isready, connection limits, disk and replication lag | Preserve volume; avoid repeated restarts and unreviewed migrations |
| Redis unavailable | memory, RDB/AOF, auth file, redis-cli ping | Treat cache vs authoritative state according to domain contract |
| Object storage errors | MinIO health, bucket policy, credentials, capacity, sampled object hashes | Do not delete destination-only objects during restore |
| Backup verification fails | artifact, sidecar, manifest, age identity and storage replication | Quarantine artifact; never restore an unverified backup |
| Migration/upgrade fails | migration logs, lock waits, schema compatibility, batch health | Stop rollout; use compensating migration or approved restore plan |
| Suspected site compromise | isolate credentials/network, preserve logs, fence old primary | Do not reconnect old primary until incident commander authorizes |
| RPO/RTO at risk | latest verified backup timestamps, recovery capacity, operator availability | Escalate early; report measured values, never target values as actuals |

## 10. Incident and change record

Record UTC timestamps, operator and approver, release commit/image digest, config revision,
backup manifest references, migration identifier, command and exit status, health evidence,
measured RPO/RTO, data reconciliation results, traffic switch/fencing proof, deviations,
follow-up owners and independent review. Do not include passwords, private keys, raw
customer data or unrestricted endpoint details. Keep detailed logs and artifacts in the
approved restricted evidence system.

## 11. Scope and safety boundaries

These scripts automate local Compose orchestration and evidence checks only. They do not
supply cloud infrastructure provisioning, HA consensus, distributed locking, DNS control,
secret-manager integration, external monitoring configuration or a live production
recovery guarantee. Validate all procedures in an isolated environment before production.
