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
authorization. Run submission/preparation, LangGraph/Harness execution,
pause/intervention, replay, connector, evaluation and quota-administration
services still require their explicit dependencies in `create_platform_app()`.
The worker currently runs the Outbox relay; it is not a general-purpose
consumer for every event topic or a LangGraph Run executor. Those integrations
must be enabled and separately acceptance-tested before claiming the complete
Agent execution journey is production-ready.

### Optional durable alert dispatcher

A separate alert delivery process is available under the Compose alerts profile.
It drains the PostgreSQL alert outbox, writes mandatory audit records before
provider delivery, retries failed sends, and stops gracefully on SIGTERM/SIGINT.
It does not share the generic Outbox-to-Redis worker lifecycle.

Configure ALERT_WEBHOOK_URL and ALERT_WEBHOOK_ALLOWED_HOSTS in the protected
deployment environment. If the provider requires authorization, mount a
read-only secret file and set ALERT_WEBHOOK_AUTHORIZATION_FILE to its container
path. The provider requires HTTPS and an exact hostname allowlist; HTTP redirects
are disabled. The service is intentionally opt-in:

    docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml --profile alerts up -d platform-alert-dispatcher

The alerts profile is not enabled by default. Do not start it until the endpoint,
allowlist, secret delivery and outbound proxy policy have been reviewed. This
process only dispatches existing durable alert events; it does not start an
alert-rule evaluator or evaluation/Prompt schedulers. Real provider delivery,
acknowledgement and multi-replica operation still require deployment evidence.

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
