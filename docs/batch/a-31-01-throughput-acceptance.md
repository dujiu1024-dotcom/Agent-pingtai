# A-31-01 — Batch throughput acceptance

## Acceptance contract

Run:

```bash
python -m unittest tests.test_issue_31_a_31_01_batch_throughput -v
```

The test measures three isolated single-task executions and uses the slowest sample as a conservative single-task baseline. It then executes 100 rows with configured concurrency (N=8), verifies all rows complete in input order, and asserts:

```text
batch_elapsed <= single_task_baseline * ceil(100 / N) * 2
```

The runner uses a fixed 50 ms asynchronous wait so the test measures executor scheduling and quota-slot overhead rather than CPU-bound model work. It also verifies that the executor reaches, but does not exceed, the configured concurrency.

## Evidence boundary

This is a deterministic in-process acceptance benchmark using the in-memory quota adapter. It validates the BatchExecutor throughput contract under a controlled asynchronous workload; it is not a production capacity benchmark and does not establish throughput for PostgreSQL contention, real model providers, network latency, or multi-worker deployment. Those require environment-specific load tests.
