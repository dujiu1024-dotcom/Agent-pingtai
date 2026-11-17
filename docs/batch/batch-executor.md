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


## Batch results and reporting (C-31-02)

Pass the immutable \`BatchExecutionResult\` to \`build_batch_report\` to obtain an
ordered summary and per-task details, aligned with the Model Eval report conventions
(pass rate and average available score).

- Summary: total/succeeded/failed/passed, pass rate, average score, total cost,
  average latency, and batch duration.
- Task detail: task ID/index/status, pass flag, optional score, cost, latency,
  output, error diagnostics, timestamps, and measured duration.
- Metric contract: successful output mappings (or objects) may expose \`passed\`
  (boolean), \`score\` (finite non-negative number), \`cost\` (finite non-negative
  number), and \`latency_ms\` (finite non-negative number). A missing pass flag
  defaults to passed for successful executions; missing score is excluded from
  the score average; missing cost is zero; missing latency falls back to executor
  duration. Failed tasks do not contribute score/cost and remain visible as failures.
- \`BatchReport.to_json()\` exports the complete summary and task details.
  \`BatchReport.to_csv()\` exports one ordered row per task, with output encoded as JSON.

The report layer is a pure projection: it does not persist results, alter execution
semantics, add an API, or introduce a scheduler.
