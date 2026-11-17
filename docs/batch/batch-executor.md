# Batch executor (C-31-01)

The generic batch executor runs one reusable `BatchTemplate` against a finite input dataset.
Each row becomes an independent task and results are returned in input order.

## Runtime contract

- `BatchTemplate` contains a stable template ID and an async runner accepting one mapping.
- `BatchExecutor.execute` requires a tenant ID and a finite sequence of 1-10,000 input mappings.
- `max_concurrency` bounds in-process fan-out with `asyncio.Semaphore`.
- Every running task also acquires a shared `QuotaConcurrencyLimiter.slot` from
  `agent_platform.quotas.concurrency`, using a configured `queue` or `worker_pool`
  concurrency budget. No second distributed scheduler or task queue is introduced.
- A task timeout or ordinary runner/quota exception is captured in that task's result;
  other dataset rows continue. Caller cancellation is propagated.
- Each task receives a unique run ID derived from the batch ID and dataset index. Quota
  reservations are released by the shared limiter context manager, including on failure.
- Results include per-task status, output or error type/message, timestamps and duration,
  plus batch totals and elapsed time.

## Composition

Configure a tenant-scoped `worker_pool` (or `queue`) budget with a hard concurrency
limit in the existing quota store, construct `QuotaConcurrencyLimiter(QuotaService(store))`,
then inject it into `BatchExecutor`. The configured shared hard limit is authoritative
across workers; the executor's semaphore additionally bounds this process.

The runner should avoid putting credentials or sensitive payloads in exception messages,
because bounded exception messages are retained as task diagnostics. Batch persistence and lifecycle APIs are provided by the workbench BatchTaskService integration (C-31-03); the executor remains a focused execution primitive.


## Batch API and workbench integration (C-31-03)

When a host supplies \`batch_service_resolver\` and the existing workbench
\`task_authorizer\` to \`create_platform_app\`, the platform mounts these
tenant-scoped endpoints under \`/api/platform/v1/workbench/batches\`:

| Method | Path | Operation |
|---|---|---|
| POST | \`/workbench/batches\` | Submit template + dataset + max_concurrency; returns 202 |
| GET | \`/workbench/batches\` | List batches visible to the actor |
| GET | \`/workbench/batches/{batch_id}\` | Query aggregate and per-task state |
| POST | \`/workbench/batches/{batch_id}/cancel\` | Cancel a queued/running batch |
| POST | \`/workbench/batches/{batch_id}/retry\` | Retry failed work using a required idempotency key |

The API validates dataset shape/size and concurrency bounds before dispatch.
It uses the authenticated Principal, the existing workbench authorizer, and a
tenant-bound service resolver; it rejects service/Principal tenant mismatches.
The \`batch_execution\` workbench capability is enabled only when the host has
configured the service resolver.

The existing #14 task-center view exposes batch submission, list/query, cancel,
and retry-failed controls. The host BatchTaskService owns durable batch records,
idempotency, dispatch to \`BatchExecutor\`, cancellation semantics, and retry
policy. This avoids introducing a second persistence/worker system in the API
adapter. Production hosts must supply a durable implementation; the API does not
silently fall back to process-local in-memory storage.
