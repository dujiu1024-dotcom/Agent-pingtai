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
because bounded exception messages are retained as task diagnostics. The executor does not
persist batches or expose submit/query/cancel/retry APIs; those belong to separate tasks.
