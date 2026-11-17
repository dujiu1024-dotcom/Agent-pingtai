# Issue #22 / A-22-05 — Graph storage performance benchmark

## Backend under test

The current production storage choice is PostgreSQL with adjacency-list relations:
`graph_entities` and `graph_relations`, tenant-scoped primary keys, B-tree indexes for entity name/type and relation source/target, and GIN indexes for JSONB attributes. The schema is provisioned by `agent_platform/storage/postgres/082_graph_storage.sql`.

## Run against a dedicated PostgreSQL benchmark tenant

Do not point this at a shared production tenant. Provision an isolated tenant first, apply migration 082, then run with a role that can write and delete only that tenant's graph rows:

```bash
export GRAPH_BENCHMARK_DSN='postgresql://...'
python scripts/benchmark_graph_storage.py \
  --tenant-id graph-benchmark-tenant \
  --workers 8 \
  --entities-per-worker 100 \
  --query-iterations 200 \
  --confirm-isolated-tenant \
  --output graph-storage-benchmark.json
```

The tool writes a unique namespace of document nodes and `references` edges through `PostgresGraphRepository`, measures concurrent entity/relation write latency and throughput, entity-prefix search and adjacency lookup p50/p95/p99/max latency, and Python allocation/process peak RSS. It redacts the DSN and deletes only rows in its generated namespace in a `finally` block. It does not apply migrations or alter indexes.

## Automated tests and limits

`tests/test_issue_22_a_22_05_graph_storage_benchmark.py` checks latency aggregation, workload coverage, and concurrency-bound execution using a fake repository. These tests validate the harness contract only; they are not production measurements.

A production acceptance report must attach the generated JSON from the actual PostgreSQL deployment, including PostgreSQL version/instance class, dataset size, index state, connection-pool settings, worker count, and observed write/query/memory metrics. Compare the same workload against alternative storage choices before concluding the PostgreSQL selection is optimal. This PR deliberately does not fabricate those measurements.
