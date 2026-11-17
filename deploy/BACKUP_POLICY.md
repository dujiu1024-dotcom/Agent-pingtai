# Backup policy and operations

This policy covers PostgreSQL, Redis, MinIO object storage, and deployment configuration. All schedule times are UTC. The machine running backups must have network access to the services, the age, sha256sum, python3, and tar tools, plus the relevant component client tools (pg_dump/psql, redis-cli, and mc).

## Schedule and retention

| Component | Cadence | Retention | Artifact |
|---|---:|---:|---|
| PostgreSQL | Hourly | 35 days | Custom-format pg_dump, age encrypted |
| Redis | Every 6 hours | 14 days | RDB snapshot, age encrypted |
| MinIO object bucket | Daily | 90 days | mc mirror archive, age encrypted |
| Deployment/configuration | Daily | 90 days | Compose, deployment env, migration runner and SQL migrations, age encrypted |

The machine scheduler should invoke the orchestrator with one component per cadence. Example cron entries (replace paths and inject credentials from the host secret manager, not a crontab file):

    0 * * * * cd /srv/agent-platform && BACKUP_COMPONENTS=postgres scripts/backup_platform.sh
    0 */6 * * * cd /srv/agent-platform && BACKUP_COMPONENTS=redis scripts/backup_platform.sh
    20 2 * * * cd /srv/agent-platform && BACKUP_COMPONENTS=objects scripts/backup_platform.sh
    40 2 * * * cd /srv/agent-platform && BACKUP_COMPONENTS=config scripts/backup_platform.sh

Do not put passwords, DSNs, MinIO keys, or age identities in crontab. Inject PG_DSN, REDIS_PASSWORD, MinIO credentials, and AGE_RECIPIENT through the host's secret manager. The configuration backup intentionally archives the deployment env file, but only inside the age-encrypted payload.

## Encryption and integrity

Every payload is age-encrypted to AGE_RECIPIENT. Keep the matching age identity offline or in a separately protected secrets manager; never store it in the backup directory or repository. During key rotation, retain old identities until every backup encrypted to the old recipient has expired.

Each artifact has a SHA-256 sidecar and JSON manifest. The manifest is published last. A backup is valid only when all three exist and the sidecar and manifest digest both match the encrypted payload. Restrict backup directory permissions to 0700 and artifacts to 0600. Use an off-host, access-controlled destination for the resulting backup directory; local disk alone is not disaster recovery.

The orchestrator applies per-component retention from deploy/backup-policy.json after a successful selected backup. Expiration deletes only matching regular .age artifacts and their sidecars; symlinks are ignored.

## Consistency and limits

- PostgreSQL pg_dump provides a transaction-consistent database snapshot.
- Redis uses an RDB snapshot. Redis is treated as rebuildable/cache and is not the sole durable record of business facts.
- MinIO mc mirror copies current object state; it is not an atomic versioned snapshot and does not preserve deleted/overwritten historical versions. Enable bucket versioning or replication separately if the workload requires historical recovery.
- The configuration archive is captured at invocation time and includes the deployment env, Compose definition, migration runner, and SQL migrations.
- These independent component backups are not a cross-component atomic checkpoint. Their timestamps must be recorded and recovery must reconcile application/database/object references before traffic is restored.
- Backup creation and checksum verification do not prove restorability. Run restore validation and record actual RPO/RTO in a separate recovery exercise; production/real-server validation is intentionally outside this task.

## Invocation

    AGE_RECIPIENT='age1...' PG_DSN='...' BACKUP_COMPONENTS=postgres scripts/backup_platform.sh
    AGE_RECIPIENT='age1...' REDIS_PASSWORD='...' BACKUP_COMPONENTS=redis scripts/backup_platform.sh
    AGE_RECIPIENT='age1...' MINIO_ENDPOINT='...' MINIO_ACCESS_KEY='...' MINIO_SECRET_KEY='...' MINIO_BUCKET='...' BACKUP_COMPONENTS=objects scripts/backup_platform.sh
    AGE_RECIPIENT='age1...' BACKUP_ENV_FILE=deploy/.env BACKUP_COMPONENTS=config scripts/backup_platform.sh

The individual component scripts in scripts/backup_{postgres,redis,object_storage}.sh remain the source of their component-specific capture commands. This orchestrator adds policy selection, encrypted configuration capture, and retention; it does not silently load executable shell from .env.
