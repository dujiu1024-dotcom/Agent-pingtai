# Platform infrastructure deployment

This runbook uses the existing `scripts/deploy_infrastructure.sh` and
`deploy/compose.yaml`; it does not introduce a second deployment system.
The Compose stack currently contains PostgreSQL, Redis and MinIO only. It does
**not** start the FastAPI application or workers. The repository does not yet
provide a production application image or complete runtime composition
entrypoint, so a healthy infrastructure stack must not be reported as a
deployed application.

## Requirements

- Docker Engine with Docker Compose v2.17+ (`docker compose up --wait`)
- A reviewed `deploy/.env` created from `deploy/.env.example`
- Outbound access to configured container registries

## First deployment

From the repository root:

```bash
cp deploy/.env.example deploy/.env
# Replace every REPLACE_WITH_* value with an independent random secret.
chmod 600 deploy/.env
scripts/deploy_infrastructure.sh --check
scripts/deploy_infrastructure.sh
docker compose --project-directory . --env-file deploy/.env -f deploy/compose.yaml ps
```

The deployment script rejects placeholder credentials and symlinked env files,
sets the env file to mode 0600, validates the fully rendered Compose config,
and stores a private snapshot of the environment used by each deployment.
Never commit `deploy/.env` or include it in an application image.

## Repeat deployment and rollback

```bash
scripts/deploy_infrastructure.sh
# Use only cached, previously reviewed images:
scripts/deploy_infrastructure.sh --no-pull
```

The script pulls configured images, starts services and waits for health
checks. A successful deployment is marked as a rollback candidate. If an
upgrade fails, the script restores the most recent successful environment
snapshot and attempts to restore service health. A failed first deployment
has no automatic rollback candidate and requires operator recovery.

Pin image versions in `deploy/.env` before a production change; avoid floating
tags. Review schema migrations and take a verified backup before upgrading.
The migration runner is `scripts/migrate_postgres.sh`; it checks migration
checksums and serializes application with a PostgreSQL advisory lock. Use
forward-compatible expand/migrate/contract changes: code rollback does not
reverse schema changes.

## Existing data protection scripts

The repository already contains separate encrypted, checksummed scripts:

- PostgreSQL: `scripts/backup_postgres.sh` / `scripts/restore_postgres.sh`
- Redis RDB: `scripts/backup_redis.sh` / `scripts/restore_redis.sh`
- Deployment secrets, Compose definition and PostgreSQL migration baseline: `scripts/backup_platform_config.sh` (age-encrypted archive; no plaintext export)
- MinIO current object state: `scripts/backup_object_storage.sh` /
  `scripts/restore_object_storage.sh`

Object backup uses `mc mirror` and does not preserve historical object
versions. The PostgreSQL and object-store scripts currently run independently;
they do not create an atomic cross-component snapshot. For a consistent
recovery point, stop API/workers and all writers before taking both backups,
record the maintenance window and timestamps, then restore PostgreSQL and
objects together. Do not claim a measured RPO/RTO until a timed restore drill
has been recorded. Redis is rebuildable transport state; PostgreSQL Outbox is
the durable event source and can republish events after Redis loss.

## Topology and diagnostics

- PostgreSQL is the durable relational source of truth; its host port is bound
  to loopback by default and its data uses the named `postgres_data` volume.
- Redis is a rebuildable transport/cache layer with AOF enabled; its host port
  is bound to loopback and its data uses `redis_data`.
- MinIO provides S3-compatible object storage; its API and console ports are
  bound to loopback and its data uses `object_data`.
- All three services use health checks, restart policies and configurable
  CPU/memory limits. A Docker volume is not a backup.

```bash
docker compose --project-directory . --env-file deploy/.env -f deploy/compose.yaml ps
docker compose --project-directory . --env-file deploy/.env -f deploy/compose.yaml logs --tail=200 postgres redis minio
```

A healthy infrastructure stack does not prove application readiness,
migration success, object-store/database consistency or disaster recovery.
