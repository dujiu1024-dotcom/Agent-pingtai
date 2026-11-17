# Platform deployment and operations

The infrastructure-only stack in `deploy/compose.yaml` contains PostgreSQL,
Redis and MinIO. It does not start the application. The production overlay
`deploy/production.compose.yaml` adds the repository-owned FastAPI and Outbox
worker entrypoints; a healthy infrastructure-only stack must not be reported
as a deployed application.

## Requirements

- Docker Engine with Docker Compose v2.17+ (`docker compose up --wait`)
- An immutable, digest-pinned application image containing this repository,
  `requirements-platform.txt`, and `uvicorn`
- A reviewed `deploy/.env.production` based on
  `deploy/.env.production.example`
- Managed PostgreSQL, Redis, MinIO secrets and outbound egress proxy

## Production composition

The production Compose overlay defaults to these repository-owned entrypoints:

- ASGI: `agent_platform.production:app`
- Worker: `python -m agent_platform.worker`

Both processes read the same PostgreSQL, Redis and HTTPS Origin configuration.
The API composes the existing PostgreSQL identity, knowledge and memory
repositories/services with the Redis-backed identity/API rate limiter. Its
readiness endpoint checks both PostgreSQL and Redis. The worker runs the
durable PostgreSQL Outbox to Redis Streams relay and writes publish audit
records to the append-only PostgreSQL audit ledger. SIGTERM/SIGINT triggers
graceful worker shutdown.

This is the initial repository-owned production composition slice, not a
claim that every optional API subsystem is wired. Run status/cancellation
routes are connected to the PostgreSQL Run state store and agent-scoped
authorization. The Workbench task API and LangGraph Run worker are installed
when a trusted deployment package passes `run_execution_builder` to
`create_production_app()`. That builder must supply the approved Registry,
snapshot, budget-enforcing model router, Vault secret resolver, reviewed Skill
store, trusted Tool handler catalog, shared quota limiter and Principal-derived
task authorizer. The repository deliberately does not load an arbitrary
factory path or invent executable graph/tool semantics. See
`docs/deployment/issue-614-run-execution-composition.md` for the contract.
Without that builder, task execution is not enabled; the Outbox worker remains
a separate PostgreSQL-to-Redis relay and is not a LangGraph Run executor.
Live Provider, Vault, checkpoint, multi-process and production execution
evidence is still required before claiming the complete Agent journey is
production-ready.

## First deployment

From the repository root:

```bash
cp deploy/.env.production.example deploy/.env.production
# Replace the image digest, hostnames, proxy and secret paths with reviewed values.
chmod 600 deploy/.env.production
docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml config
docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml up -d --wait
docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml ps
```

The Compose file requires an immutable image reference and explicit secret
files. Never commit `deploy/.env.production` or include it in an application
image. Apply PostgreSQL migrations using `scripts/migrate_postgres.sh` before
starting application traffic; the production composition does not silently
run schema migrations during process import.

## Repeat deployment and rollback

Pin image versions in `deploy/.env.production` before a production change;
avoid floating tags. Review schema migrations and take a verified backup
before upgrading. Use forward-compatible expand/migrate/contract changes:
code rollback does not reverse schema changes.

## Existing data protection scripts

The repository already contains separate encrypted, checksummed scripts:

- PostgreSQL: `scripts/backup_postgres.sh` / `scripts/restore_postgres.sh`
- Redis RDB: `scripts/backup_redis.sh` / `scripts/restore_redis.sh`
- Deployment secrets, Compose definition and PostgreSQL migration baseline:
  `scripts/backup_platform_config.sh` (age-encrypted archive; no plaintext export)
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
- Stateful services and application workloads use health checks, restart
  policies, CPU/memory limits and restricted container privileges. A Docker
  volume is not a backup.

```bash
docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml ps
docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml logs --tail=200 platform-api platform-worker postgres redis minio
```

A healthy infrastructure stack does not prove migration success, object-store/
database consistency, all optional subsystem availability or disaster recovery.
