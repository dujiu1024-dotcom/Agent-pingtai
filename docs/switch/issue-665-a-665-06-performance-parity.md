# Issue #665 / A-665-06 — Performance parity acceptance gate

A-665-06 requires the new platform to be no slower than legacy SQLite for comparable Run throughput, Blackboard QPS, Redis queue p95 latency, and PostgreSQL connection-pool wait p95. The evaluator `scripts/evaluate_switch_performance_parity.py` compares paired JSON reports and fails closed.

Each report must contain identical workload fields (`workers`, `operations_per_worker`, `dataset_size`, `warmup_operations`, `measurement_seconds`), environment fields (`cpu_model`, `cpu_count`, `memory_mb`, `os`, `python_version`, `storage_profile`), and positive metrics (`run_throughput_ops_per_second`, `blackboard_qps`, `redis_queue_latency_p95_ms`, `postgres_pool_wait_p95_ms`). Throughput/QPS must not decrease; p95 latency/wait must not increase. Default allowed regression is zero.

Run:

```bash
python scripts/evaluate_switch_performance_parity.py \
  --legacy evidence/legacy-performance.json \
  --platform evidence/platform-performance.json
```

Exit codes: 0 = pass, 1 = measured regression, 2 = incomplete/incomparable evidence. Missing fields or mismatched workload/hardware are never interpreted as pass.

Current evidence is insufficient: the repository has a synthetic SQLite-shaped baseline and isolated PostgreSQL/Redis probes, not paired measurements from the same workload, dataset, hardware, and measurement window. This PR delivers the fail-closed parity gate, not fabricated production numbers. A-665-06 remains performance acceptance pending until complete paired reports are collected and the evaluator returns `pass`.
