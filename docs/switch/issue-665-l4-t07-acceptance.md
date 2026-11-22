# L4-T07 — Switch end-to-end performance and failover acceptance

## Automated CI acceptance

The existing Platform Release Gate already runs the Issue #665 end-to-end journey, SQLite/PostgreSQL/Redis benchmark probes, deterministic dependency fault drills, and the PostgreSQL receipt + Redis Streams worker-recovery integration suite. L4-T07 adds a fail-closed evidence assembler; it does not duplicate those test implementations.

After those test commands succeed, CI runs:

```bash
python scripts/verify_switch_l4_t07_acceptance.py \
  --benchmark switch-performance-benchmark.json \
  --output switch-l4-t07-acceptance.json
```

The assembler rejects missing PostgreSQL or Redis measurements, mismatched operation counts, and incomplete latency distributions. It records Run lifecycle transaction throughput/latency, Blackboard write/read throughput/latency, Redis publish→consumer-read→ACK latency, and the deterministic failover paths exercised by the same successful CI job. Both JSON reports are uploaded as the `issue-665-switch-l4-t07-acceptance` artifact.

## Failover coverage

The report is emitted only after the existing CI test commands have passed. They cover:

- Redis publish outage → circuit breaker → process-local replay-only degraded path.
- PostgreSQL primary connection outage → quota/model dispatch blocked → simulated standby promotion → one successful dispatch.
- Vault outage → secret resolution fails closed; no stale secret fallback.
- PostgreSQL receipt lease expiry + Redis pending entry → worker restart → replay and completion.

These are deterministic adapter-level failover drills. They do not stop, promote, or reconfigure shared infrastructure.

## Performance interpretation

CI measures the same synthetic Run transaction and Blackboard write/read operation shapes against a temporary SQLite baseline and ephemeral PostgreSQL service, plus a bounded concurrent Redis publish/read/ACK probe. The evidence assembler requires every backend to report the expected operation count and latency percentiles.

The benchmark is diagnostic only. GitHub-hosted runner CPU/storage and ephemeral service topology do not provide a matched production environment fingerprint. Therefore the report deliberately records `parity_conclusion: incomplete`; it does not claim new-platform performance is equal to or faster than the legacy system.

## Explicitly not tested

No live PostgreSQL HA promotion, Redis cluster/process failover, Vault cluster switchover, production traffic, production credential, customer data, external server, or physical device is used. Production failover/RTO and same-hardware legacy-vs-platform performance parity still require separately approved deployment-side evidence.
