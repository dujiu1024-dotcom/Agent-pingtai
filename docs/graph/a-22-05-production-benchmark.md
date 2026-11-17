# A-22-05 PostgreSQL graph production benchmark

This is a repeatable benchmark harness, not a claim that production performance has
already passed. The graph backend currently uses PostgreSQL adjacency tables and
B-tree indexes; no Neo4j/Neptune comparison is inferred from this measurement.

## Run against an isolated environment

1. Apply the repository PostgreSQL migrations, including
   `agent_platform/storage/postgres/090_graph_storage.sql`.
2. Create or select a dedicated, empty benchmark tenant in that database.
3. Set `AGENT_PLATFORM_BENCH_DSN` and `AGENT_PLATFORM_BENCH_TENANT_ID`.
   Optionally set `AGENT_PLATFORM_BENCH_PROJECT_ID` to exercise project-scoped rows.
4. Install `requirements-platform.txt` and run:

```bash
python scripts/benchmark_graph_postgres.py \
  --entities 5000 --relations 10000 --query-samples 200 \
  --query-concurrency 8 --output graph-benchmark.json
```

The harness inserts namespaced synthetic entities/relations, measures concurrent
batch-write throughput, tenant/type/entity indexed lookups, outgoing adjacency
queries, p50/p95/p99 latency, and PostgreSQL relation size. It records server
version and key memory/connection settings. All rows are tagged with a unique
benchmark run ID and deleted in a `finally` cleanup path.

**Safety:** use a disposable/isolated benchmark tenant only. The script refuses
to create a tenant, does not touch unrelated rows, and removes only rows carrying
its unique run marker. A forced process termination may leave tagged rows; clean
up by filtering `attributes->>'benchmark_run'` for the reported run ID.

## Evidence and acceptance

Archive the JSON report with the exact database instance class, storage type,
PostgreSQL version, dataset size, warm-up policy, concurrency, and deployment
revision. Repeat at least three times on the production-equivalent topology and
report median p95 across runs. Include CPU, memory, disk IOPS and pool settings
from the deployment environment; relation-size bytes are not a substitute for
process RSS. Compare against an explicitly documented production SLO. No
universal latency threshold is invented by this harness.

The CI contract tests validate report percentile math only. CI fixtures and
synthetic data do not satisfy the required production-environment benchmark.
A-22-05 remains field-pending until real environment reports and an operator
sign-off are attached to Issue #267.
