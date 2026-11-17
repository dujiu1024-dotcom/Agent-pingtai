# Production environment and deployment contract

## Topology

`production.compose.yaml` describes one API service and three stateful dependencies:

- **API:** stateless ASGI replicas; expose only through a TLS reverse proxy/load balancer. The sample publishes its port on loopback for a host proxy.
- **PostgreSQL + pgvector:** durable system of record on a named volume.
- **Redis:** shared coordination, rate-limit and stream infrastructure; AOF enabled and `noeviction` selected so memory pressure is surfaced rather than silently evicting coordination keys.
- **MinIO:** private object storage on a named volume.

All services share a private Compose bridge. Database, Redis and object-store ports are bound to loopback for operator access; do not expose them directly to the public network. For multi-host/high-availability production, move the stateful dependencies to managed PostgreSQL/Redis/S3-compatible services and run multiple stateless API replicas behind an external load balancer. Compose is a single-host reference topology, not a high-availability scheduler.

## Configuration and secret injection

1. Copy `.env.production.example` to `.env.production`; replace the example image digest and ASGI import path.
2. Create the three secret files at the configured paths using the host secret manager. Each file contains only its secret value and a trailing newline. Restrict them to the deployment operator (for example directory mode 0700 and file mode 0400).
3. Set exact HTTPS origins, immutable image digest, database name/user, resource budgets and secret-file paths. Protect `.env.production` with mode 0600. Never commit it or secret files.
4. Validate and deploy:

   ```sh
   docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml config --quiet
   docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml pull
   docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml up -d --wait
   docker compose --env-file deploy/.env.production -f deploy/production.compose.yaml ps
   ```

Compose secrets are mounted under `/run/secrets`. The API receives *_FILE paths; the deployment-owned ASGI bootstrap must read those files and construct the PostgreSQL, Redis and object-storage clients. Do not copy secret values into environment variables or logs.

## Application image and bootstrap boundary

The repository provides `create_platform_app`, not a universal zero-configuration production ASGI instance: identity, repositories, knowledge/memory services, Redis and authorizers are injected at composition time. Therefore `PLATFORM_ASGI_APP` must name a deployment-owned importable ASGI object (for example `deployment.bootstrap:app`) that wires durable adapters, runs/validates migrations as a separate release step, and supplies readiness checks for **PostgreSQL, Redis and object storage**. The API image must contain the project package, `requirements-platform.txt`, Uvicorn and that bootstrap module. Do not point production at a test fixture or in-memory adapter.

The application factory registers `/health/live` and `/health/ready`. Liveness checks only process responsiveness. Readiness executes injected dependency checks; a missing check or unavailable dependency returns HTTP 503. The default factory readiness check covers Redis only, so a production bootstrap must pass explicit PostgreSQL and object-storage checks as well. The container healthcheck uses readiness and Compose waits for the three dependencies to become healthy before starting the API.

## Resource and process limits

Default container ceilings in the reference overlay:

| Service | Memory | CPU |
|---|---:|---:|
| API | 2 GiB | 2 |
| PostgreSQL | 4 GiB | 4 |
| Redis | 1 GiB | 1 |
| MinIO | 2 GiB | 2 |

Override via the corresponding *_MEMORY_LIMIT and *_CPU_LIMIT variables after load testing. API runs as UID/GID 10001, read-only root filesystem, bounded tmpfs, dropped Linux capabilities, no-new-privileges and PID limit. Database and object-store volumes require independent backup and capacity monitoring. Resource limits are starting ceilings, not capacity guarantees; size PostgreSQL shared memory, connection pool, API worker count, Redis maxmemory and object-store disk from measured workload.

## Health, startup and operational boundaries

- PostgreSQL, Redis and MinIO each have a native healthcheck and persistent named volume.
- API startup depends on all three dependencies being healthy; the API healthcheck uses `/health/ready`.
- A failed readiness probe must remove an API instance from load-balancer traffic. Liveness and readiness must not be conflated.
- Apply schema migrations once as a controlled release job before enabling new application replicas; do not run concurrent migrations from every API worker.
- Configure a TLS reverse proxy, external DNS, certificate renewal, network firewall, off-host backups, monitoring and alert delivery outside this single-host Compose file.
- Production secrets and stateful volumes must not be baked into images or source control.

This task supplies deployment definitions and contracts. It does not claim a live production rollout, managed-service provisioning, or HA/failover validation.
