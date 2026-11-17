# Platform Operations Runbook

This document is the operator-facing procedure for deploying, upgrading, backing up,
restoring, rolling back and verifying the Agent Platform. It complements
[`BACKUP_POLICY.md`](../deploy/BACKUP_POLICY.md) and the
[disaster-recovery runbook](disaster-recovery-runbook.md). It does not authorize
production changes by itself. Follow the environment's change approval, maintenance
window, access-control and incident procedures.

## 1. Operating rules

- Pin application images to immutable tags or digests. Do not deploy `latest`.
- Keep secrets in the host secret manager or protected environment file. Never put
  credentials in Git, command-line arguments, CI logs, shell history or evidence.
- Keep the previous known-good image digest, deployment configuration revision,
  migration revision and backup identifiers available until the change is accepted.
- Take and verify a fresh backup before schema-changing upgrades.
- Database migrations must be backward-compatible with the previous application
  during the rollback window. If a migration is destructive or irreversible, require
  a separately approved forward-fix/recovery plan; restoring an old database is a
  data-loss decision, not a routine application rollback.
- Do not combine deployment, database restore and traffic failover in one unreviewed
  action. Keep application writers stopped during a restore.
- The commands below are templates. Substitute the deployment's approved Compose
  file, project name, image references and service names. Never infer production
  topology from this repository's development defaults.

## 2. Host and configuration preflight

Run from the checked-out release directory using the deployment operator account:

```bash
python3 --version
docker --version
docker compose version
python3 scripts/ops_check.py preflight \
  --require-command docker \
  --require-command python3 \
  --env-file /etc/agent-platform/platform.env \
  --require-env PG_DSN \
  --require-env AGE_RECIPIENT
```

The helper checks command presence and required environment-variable *names* only;
it never evaluates the env file or prints values. Add the environment-specific names
needed by the selected services (Redis, MinIO, identity provider, model gateway).
Protect the file and parent directory (for example, mode 0600 and root/operator-only
ownership). Confirm secret-manager access separately without printing secret values.

Before any change, record:

- release tag and immutable image digest for every service;
- Git commit and deployment configuration revision;
- current migration/schema version;
- current service health, error rate, latency and queue/outbox lag;
- backup set identifiers and the off-host destination;
- approved change/incident ticket, operator and independent reviewer.

Validate the rendered deployment configuration without starting containers:

```bash
docker compose --env-file /etc/agent-platform/platform.env \
  -f /etc/agent-platform/compose.yml config --quiet
```

If the installed Compose version does not support `--quiet`, render to a protected
temporary file and inspect it locally; do not publish rendered configuration because
it may contain interpolated secrets.

## 3. Deploy and upgrade

1. Confirm approval, maintenance window, capacity, dependencies and rollback owner.
2. Capture a pre-change backup set (Section 4), verify each selected artifact and
   copy it to the approved off-host destination.
3. Record the currently running image digests and configuration revision.
4. Fetch only the approved immutable release:

   ```bash
   docker compose --env-file /etc/agent-platform/platform.env \
     -f /etc/agent-platform/compose.yml pull
   ```

5. Review release notes and migration plan. Confirm migrations are additive and
   compatible with the previous application version, or obtain explicit approval for
   the documented recovery strategy.
6. Apply migrations using the deployment's versioned migration job/entrypoint.
   Do not improvise SQL or run migrations concurrently from multiple replicas.
7. Start/update services in dependency order using the approved Compose project:

   ```bash
   docker compose --env-file /etc/agent-platform/platform.env \
     -f /etc/agent-platform/compose.yml up -d --remove-orphans
   ```

8. Run Section 6 checks. Keep traffic at canary/limited weight until readiness,
   tenant isolation, queue processing and error/latency signals are acceptable.
9. Expand traffic under observation. Record release digests, migration result,
   health evidence, start/end timestamps and reviewer sign-off.

Do not use `down -v`, anonymous volume pruning, or destructive cleanup as an
upgrade shortcut. Do not remove the previous image/configuration until the rollback
window has closed.

## 4. Backup and backup verification

The backup orchestrator and per-component scripts are documented in
[`BACKUP_POLICY.md`](../deploy/BACKUP_POLICY.md). Schedule the component cadence
from the policy (PostgreSQL hourly, Redis every six hours, objects/config daily).
Inject credentials through the host secret manager, not crontab.

Example invocations (use one component per scheduled run):

```bash
BACKUP_COMPONENTS=postgres scripts/backup_platform.sh
BACKUP_COMPONENTS=redis scripts/backup_platform.sh
BACKUP_COMPONENTS=objects scripts/backup_platform.sh
BACKUP_COMPONENTS=config BACKUP_ENV_FILE=/etc/agent-platform/platform.env \
  scripts/backup_platform.sh
```

Set `BACKUP_DIR` to the restricted backup volume and provide each component's
required credentials and `AGE_RECIPIENT` through the approved secret injection
mechanism. Confirm that artifacts are copied to a separate failure domain.

Verify a selected artifact before relying on it:

```bash
python3 scripts/ops_check.py verify-backup /secure/backups/platform-<UTC>.dump.age
```

The verifier requires the encrypted artifact, SHA-256 sidecar and JSON manifest;
it checks the sidecar digest, manifest digest, artifact name and format. Repeat for
each component artifact. A successful integrity check is not a restore test and does
not prove cross-component consistency or decryptability. Perform isolated restore
drills according to the disaster-recovery runbook and retain measured evidence.

## 5. Restore and disaster recovery

Restores are destructive and require an incident/change approval, a selected recovery
point, an independent reviewer and a recovery environment. Never test against the
production target. Follow the full ordered process in
[`disaster-recovery-runbook.md`](disaster-recovery-runbook.md).

Minimum operator sequence:

1. Declare the incident/change and freeze deployments, API writers, scheduled jobs
   and queue consumers. Preserve logs and record the freeze timestamp.
2. Provision/validate dependencies and restore configuration/secrets from the
   approved encrypted configuration backup.
3. Verify the PostgreSQL, Redis and object artifacts independently with
   `scripts/ops_check.py verify-backup`; confirm manifest identity, encryption
   identity and target compatibility.
4. Create `$BACKUP_DIR/RESTORE-READY` only after the application writers are stopped
   and the selected artifacts/recovery order have been reviewed.
5. Run `scripts/restore_platform.sh` with all required explicit confirmations and
   component artifact paths. The script restores PostgreSQL, Redis, then objects;
   it does not provision infrastructure, stop services, perform replica promotion,
   switch DNS or validate application semantics.
6. Keep writers and traffic stopped. Validate migrations, database invariants,
   Redis state, object references/hashes, outbox/queue state, tenant isolation and
   application readiness. Reconcile snapshot skew before proceeding.
7. Resume workers and traffic gradually only after approval; use a canary and monitor
   errors, latency and queue lag. Record actual RPO/RTO and independent sign-off.

Stop on missing metadata, digest/decryption failure, incompatible database identity,
unsafe archive paths, unexpected data loss, unverified fencing or failed consistency
checks. Never bypass a confirmation token to meet an RTO target.

## 6. Post-deploy verification

Run checks appropriate to the deployment; do not treat a running container as proof
of application correctness.

```bash
docker compose --env-file /etc/agent-platform/platform.env \
  -f /etc/agent-platform/compose.yml ps
python3 scripts/ops_check.py verify-health --url https://<approved-host>/health
python3 scripts/ops_check.py verify-health --url https://<approved-host>/ready
```

Then verify:

- database migration head equals the release's expected schema revision;
- API liveness and readiness succeed; dependency health is not masked as ready;
- authenticated smoke tests succeed for at least two isolated tenants, with no
  cross-tenant reads/writes;
- login/authorization, audit append-only behavior and approval gates work;
- queue consumers process a canary event once; retries and dead-letter behavior
  are observable; outbox lag returns to baseline;
- object upload/download and checksum verification work with scoped credentials;
- metrics, logs, traces and alerts are flowing; no new critical error or saturation;
- backup jobs are scheduled and the latest artifact set is complete and off-host.

The HTTP helper performs a read-only request and accepts 2xx/3xx responses. Use the
platform's authenticated smoke-test harness for protected readiness endpoints; do
not place bearer tokens in URLs or command history.

## 7. Rollback

Rollback is permitted only when the previous application version is compatible with
the current schema and data. The release owner must explicitly choose application
rollback versus database recovery.

1. Stop rollout and hold traffic at the current safe weight; preserve logs and
   deployment evidence.
2. Confirm the previous immutable image digest and configuration revision.
3. Check migration compatibility. If schema changes are backward-compatible, restore
   the previous image/configuration without reverting database data.
4. If schema/data changes are not backward-compatible, stop and invoke the approved
   recovery plan. Do not run ad hoc reverse SQL or restore an old database without
   an explicit data-loss assessment and incident approval.
5. Re-deploy the previous immutable release using the same Compose project and
   protected env file; verify readiness, tenant isolation, queue/outbox and data
   integrity before restoring traffic.
6. Record the reason, affected versions, data implications, actual recovery time,
   evidence and follow-up corrective action.

Example application-only rollback (replace the image tag/digest in the protected
release env file, then validate config and apply):

```bash
docker compose --env-file /etc/agent-platform/platform.env \
  -f /etc/agent-platform/compose.yml config --quiet
docker compose --env-file /etc/agent-platform/platform.env \
  -f /etc/agent-platform/compose.yml up -d
```

Do not use `docker compose down -v` as a rollback method.

## 8. Troubleshooting

| Symptom | Checks | Safe response |
| --- | --- | --- |
| Compose config fails | file path, variable names, image reference, Compose version | fix protected configuration; do not print interpolated config to CI/logs |
| Image pull denied | registry login, image digest, network/registry policy | re-authenticate through secret manager; do not switch to `latest` |
| Service unhealthy | container logs, dependency readiness, resource pressure, recent config diff | hold traffic; fix dependency/config or roll back compatible image |
| Migration fails | migration lock, database connectivity, schema head, migration logs | stop rollout; do not rerun unknown partial SQL; follow migration-specific recovery |
| PostgreSQL backup invalid | sidecar, manifest, age recipient, artifact transfer integrity | quarantine artifact and select another verified recovery point |
| Redis restore rejected | Redis stopped, RDB format/version, owner/mode, target path | keep Redis stopped; validate target and restore artifact before retry |
| Object restore mismatch | bucket identity, archive manifest, endpoint credentials, destination inventory | keep traffic stopped; reconcile object keys; do not enable delete-extra mode by default |
| Health endpoint times out | DNS/TLS, routing, app logs, dependency saturation | preserve canary/traffic hold and investigate; do not disable readiness gates |
| Queue/outbox lag grows | consumer health, pending entries, retry/DLQ counts, DB connectivity | pause expansion; resolve idempotently and inspect receipts before replay |

## 9. Change and drill evidence

Every deployment, rollback, backup validation and restore drill should record:

- ticket/change ID, environment, operator, reviewer and approval timestamps;
- release commit, image digests, config revision and schema/migration revision;
- backup artifact names, manifest IDs, checksums and off-host location (never secrets);
- start/end timestamps, health and smoke-test results, queue/outbox observations;
- actual RPO/RTO for recovery exercises, data gaps and reconciliation outcome;
- rollback decision, customer impact, follow-up owner and completion date.

Use the dedicated disaster-recovery drill template for timed recovery exercises.
A document or mocked test is not evidence of a production restore. No production
deployment, backup, restore, rollback or failover is executed by this runbook change.
