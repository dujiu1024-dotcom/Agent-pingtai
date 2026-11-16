# Platform infrastructure deployment

This runbook operates the existing `deploy/compose.yaml` stack. It currently
contains PostgreSQL, Redis and MinIO only; it does **not** start the FastAPI
application or workers. The repository has no production application image or
complete runtime composition entrypoint yet, so do not interpret a healthy
infrastructure stack as a deployed application.

## Requirements

- Docker Engine with Docker Compose v2.17+ (`docker compose up --wait`)
- Python 3.11+
- Outbound access to the configured container registries

## First deployment

From the repository root:

```bash
python scripts/deploy_platform.py --init-only
# Review deploy/.env; the script generated independent random passwords.
python scripts/deploy_platform.py --check
python scripts/deploy_platform.py
docker compose --project-directory . --env-file deploy/.env -f deploy/compose.yaml ps
```

The generated `deploy/.env` is created with mode 0600 and is never overwritten.
Back it up separately using an approved secrets-management process. Never commit
it or include it in an application image.

## Repeat deployment

```bash
python scripts/deploy_platform.py
```

The script validates required secrets, validates the fully rendered Compose
configuration, pulls configured images, starts services, waits for health
checks, and prints service status. Use `--no-pull` only when intentionally
deploying already-cached image versions. Use `--check` for a non-mutating
configuration check.

## Topology and operational boundaries

- PostgreSQL is the durable relational source of truth; its host port is bound
  to loopback by default and its data uses the named `postgres_data` volume.
- Redis is a rebuildable transport/cache layer with AOF enabled; its host port
  is bound to loopback and its data uses `redis_data`.
- MinIO provides the S3-compatible object service; its API and console ports
  are bound to loopback and its data uses `object_data`.
- All three services use health checks and restart policies. Resource limits
  are configurable through Compose environment overrides where supported by
  the host's Compose implementation.
- Keep the database, Redis and object-store volumes on durable storage. A
  Docker volume is not a backup.

## Upgrade and rollback

Pin image versions in `deploy/.env` before a production change; avoid floating
tags. Review schema migrations and take a verified backup before upgrading.
Upgrade services with the same deployment command. Roll back by restoring the
previous image references and rerunning it. Application/schema rollback is
not automated by this infrastructure-only stack; use forward-compatible
(expand/migrate/contract) migrations and restore a separately verified database
backup only after impact review.

## Health and diagnostics

```bash
docker compose --project-directory . --env-file deploy/.env -f deploy/compose.yaml ps
docker compose --project-directory . --env-file deploy/.env -f deploy/compose.yaml logs --tail=200 postgres redis minio
```

A healthy PostgreSQL/Redis/MinIO stack does not prove application readiness,
migration success, object-store/database consistency, or disaster recovery.
Those require the separate backup/restore runbook and a recorded restore drill.
