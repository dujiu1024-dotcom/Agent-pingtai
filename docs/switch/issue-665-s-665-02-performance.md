# S-665-02 — performance benchmark harness

## Scope and commands

The benchmark is deliberately opt-in and does not start services, contact external endpoints, or read the production SQLite database.

Run the legacy SQLite baseline:

```bash
python scripts/benchmark_switch_performance.py --workers 8 --operations 100
```

The harness creates a temporary SQLite database and measures:
- Legacy-shaped Run lifecycle transactions: insert, queued→succeeded update, read.
- Blackboard write/read operations.
- Throughput and p50/p95/p99/max operation latency.

Optional platform probes require explicit operator-supplied environment variables:

```bash
AGENT_PLATFORM_BENCH_DSN='postgresql://...' \
AGENT_PLATFORM_BENCH_REDIS_URL='redis://...' \
python scripts/benchmark_switch_performance.py --workers 8 --operations 100
```

- PostgreSQL: bounded `psycopg_pool.AsyncConnectionPool` checkout + `SELECT 1` under concurrent load; reports pool stats and checkout/query latency.
- Redis: concurrently publishes unique events to a namespaced Redis Stream, reads them through a consumer group with up to the configured worker count, ACKs each event, and records publish-to-consumer-ACK p50/p95/p99/max latency plus observed messages/second. The probe uses a unique tenant/stream prefix and does not flush shared keys.

## Evidence interpretation

The SQLite baseline is executable without services and uses a temporary database. It is not a production snapshot and does not represent LLM/model execution.

The PostgreSQL probe is a connection-pool pressure probe, not a full Run transaction or Blackboard repository benchmark. Redis is a concurrent queue probe only when an explicit URL is supplied; its throughput is queue publish/read/ACK throughput, not complete Run execution QPS. CI must not claim these external probes ran merely because the script was tested.

No platform-vs-SQLite performance parity conclusion is possible unless PostgreSQL and Redis probes are run against an explicitly provisioned, isolated benchmark environment and their results are retained. Missing services are reported as `not_run`; no mock timings are generated. No production server, external resource, or real credential is requested by this change.

## CI test

```bash
python -m unittest tests.test_issue_665_s_665_02_benchmark -v
ruff check scripts/benchmark_switch_performance.py tests/test_issue_665_s_665_02_benchmark.py
```
