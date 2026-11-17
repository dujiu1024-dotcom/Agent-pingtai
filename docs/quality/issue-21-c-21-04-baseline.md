# C-21-04 Quality and capacity baseline

This benchmark contract is intentionally provider-neutral. CI runs deterministic
synthetic workloads; it never contacts a model vendor, vector database, Redis,
PostgreSQL, production server, or customer environment.

## Fixed inputs and repeatability

- Supply a versioned tuple of `BenchmarkCase` records. Prompts and expected
  anchors are synthetic and contain no customer data.
- `dataset_fingerprint` hashes canonical JSON (suite version, case IDs,
  prompts, expected/forbidden terms, and expected document IDs). A baseline
  comparison fails closed when fingerprints differ.
- Set iterations and concurrency explicitly. The runner uses an asyncio
  semaphore, isolates per-case failures, and sorts observations for stable
  serialization.

## Metrics

- Capacity/performance: completed/failed count, throughput, p50/p95/p99 latency,
  requested concurrency and total duration.
- Model cost: input/output token totals and estimated USD cost using explicit
  per-million-token prices. Zero prices mean cost is intentionally unpriced,
  not free in production.
- Answer quality: expected/forbidden anchor pass rate and mean score.
- RAG: Recall@5 and mean reciprocal rank (MRR) over expected document IDs.
- Evidence: per-case observations and SHA-256 dataset fingerprint.

## Regression gate

`compare_quality_capacity` checks dataset identity, p95 latency ratio, estimated
cost ratio, answer pass-rate delta, and RAG recall delta. Tune
`RegressionPolicy` in a reviewed baseline change; do not silently replace a
dataset version or lower quality thresholds to make a run pass.

## Execution modes and limits

CI uses in-process async adapters to verify calculation, concurrency bounds,
failure isolation, deterministic dataset identity, and regression detection.
Provider-backed model latency/cost and live RAG retrieval are opt-in operator
runs and are deliberately not started by this PR. The report's cost is an
estimate from caller-supplied token prices, not a billing statement. Human
preference, citation entailment, and semantic correctness require a separately
curated evaluation set and human review; lexical anchors are a screening signal.
