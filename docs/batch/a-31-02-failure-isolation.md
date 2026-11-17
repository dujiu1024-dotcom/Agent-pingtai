# A-31-02 — Batch failure isolation acceptance

## Automated acceptance

Run:

```bash
python -m unittest tests.test_issue_31_a_31_02_batch_failure_isolation -v
```

The acceptance suite exercises the existing `BatchExecutor` through its public API:

- A runner raising Python `MemoryError` is recorded as one failed task; the other dataset rows still execute and succeed.
- A runner exceeding the configured timeout is cancelled; its task is recorded as `TimeoutError`; the concurrency slot is released and later rows still execute.
- Batch totals and ordered per-task results account for both failures without aborting the batch.

The test uses the in-memory quota adapter to isolate executor behavior. It is an automated contract test, not a production memory-pressure or process-supervisor test.

## OOM boundary

Python `MemoryError` is an exception within the worker process and is covered by the executor's per-task exception boundary. An operating-system/container OOM kill terminates the process and cannot be caught by an in-process `asyncio` task; surviving that event requires an external worker supervisor and durable batch/task state. This PR does not claim to validate or implement process-level OOM recovery.

Real container memory limits, kernel OOM killer behavior, and production worker restart/reconciliation remain deployment-level evidence and are not simulated here.
